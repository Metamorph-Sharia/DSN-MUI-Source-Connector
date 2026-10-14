> **2026-10-14:** the team adopted a Python connector interface
> (`CONNECTOR_INTERFACE.md` v1.0, shared by the OJK/BI side). The
> orchestrator-facing surface of this repo is **`dsn_ingest/adapter.py`**
> (`DsnMuiConnector`, using the unmodified root `source_connector.py`).
> The CLI contract documented below stays as the human/scheduler surface
> and the debug tool; both share the same internals.

# Source Connector Contract

Contract between the per-source repos (`DSN-MUI-Source-Connector`,
`OJK-Source-Connector`, `BI-Source-Connector`, …) and whatever orchestrator
we build later. **No shared code.** Compatibility = same verbs, same output
semantics, same error vocabulary. Language/framework per repo is free
(Python + requests + BeautifulSoup is enough for dsnmui.or.id today).

## The five modules → five verbs

| Module                        | Verb        | What it does                                          |
|-------------------------------|-------------|-------------------------------------------------------|
| 1. Source discovery           | `discover`  | crawl listing/index pages → remote inventory, no downloads |
| 2. Document acquisition       | `sync`      | download with fallback ladder (see below)             |
| 3. Extraction & normalization | `sync`      | PDF → text layer extraction (`needs_ocr` if no text layer) |
| 4. Dedup & change detection   | `sync`      | sha256 per document → `new` / `changed` / `unchanged` |
| 5. Corpus monitoring          | `check`, `status` | liveness probe, last runs, error breakdown, coverage |

Plus: `list` = dump everything we currently hold (alignment vs `discover`).

## Invocation (how an orchestrator runs any connector)

```bash
uv run --directory <connector-repo> dsn sync --out <delta.jsonl>
uv run --directory <connector-repo> dsn status
```

The orchestrator shells out per repo and merges JSONL streams — that's the
whole integration story. No imports, no shared venv, language per repo free.


## Two output styles (both important, different contexts)

- **`sync` → DELTA only.** Prints JSONL for `new`, `changed`, `failed` —
  nothing else. Use when feeding a pipeline / running on a schedule.
  `--out FILE` appends the delta to a growing corpus file.
- **`list` → FULL SNAPSHOT.** One JSONL line per item in the local corpus,
  status ∈ `unchanged | failed | missing` (missing = discovered remotely,
  never acquired). Use when asking *"do we have everything?"*.

**Alignment check** = reconcile `discover` (remote) against `list` (local).
`status` shortcut prints both sides in one JSON doc:
`{"remote_indexed": 166, "never_acquired": 0, "failed_by_error_class": {"auth": 1}, "aligned": true, "runs": [...]}`

## Record shape — deliberately NOT a frozen schema yet

Goal right now is *retrieving raw text*. Records are **pointers**, not
payloads: the actual bytes/text live in files on disk.

Common fields every connector should carry (names may differ until we
merge — that's fine, mapping is a one-time job):

```
connector    source_id   title   status        pdf|file_url (EXACT, incl. params)
sha256       size_bytes  content_type   raw_path   text_path   needs_ocr
error        error_message   at (timestamp)
metadata…    (free: fatwa number, regulation number, year, …)
```

⚠️ `source_id` must be **stable across runs** (e.g. `162-DSN-MUI-VII-2025`,
document number — not URL with query params). Everything downstream
(dedup, change detection, "what's new?") hangs off it.

## Error vocabulary & the fallback ladder

One bad document NEVER crashes a run. It becomes a delta record with
`status: "failed"`, `error: <class>`, and is **auto-retried next sync**.
Failed acquisition never deletes the last good copy (content-addressed
blob store keeps versions forever).

| class       | meaning / response                                                   |
|-------------|-----------------------------------------------------------------------|
| `transient` | timeout / conn reset / 5xx → retried w/ exponential backoff, then logged |
| `quota`     | 429 rate limit → respect `Retry-After`, slow the run                  |
| `auth`      | server demands sign-in → anonymous fallbacks exhausted (incl. Drive `resourcekey` + consent-form resubmit). Human grabs it once, or add a mirror as alternate URL |
| `not_found` | link rot / file gone / pagination end                                |
| `content`   | got a response but not the expected document (HTML where a PDF should be) |
| `gate`      | website bot-interstitial — retried with longer waits; connector falls back to its stored index so document acquisition continues |
| `unknown`   | unforeseen → still just one failed item                               |

External-server / permission errors are therefore *classified, logged, and
scheduled for retry* — not exceptions that kill the pipeline.

## Scanned documents (the OJK/BI problem)

DSN fatwas are born-digital → plain text extraction, no OCR.
If your source has **scanned** PDFs: still run the same connector.
Text extraction will return near-empty output → set `needs_ocr: true`,
keep `raw_path`, ship the record. OCR is a *separate downstream job* that
reads the blobs we archived — never a blocker for the retrieval pipeline.

## Exit codes

`0` ok · `1` partial (some docs failed / not aligned) · `2` run-level failure
(check failed, discovery died, etc.)

## How the orchestrator invokes a connector

Each connector lives in its own repo; the orchestrator just shells out per
repo — no imports, no shared venv, connector language is irrelevant as long
as the verbs match:

```bash
uv run --directory ../OJK-Source-Connector ojk sync --out corpus/ojk.jsonl
uv run --directory ../DSN-MUI-Source-Connector dsn sync --out corpus/dsn.jsonl
```

State (DB, blobs, text) stays inside each connector repo's `data/` dir —
the JSONL delta + `status` output is the only thing the orchestrator needs.

## Run-report (stderr on `sync`)

On a terminal, `sync` prints one line per document as it finishes, plus a summary.
Failures print in every mode, so automated logs still show what broke. Use
`--progress` to force the full output and `--quiet` to suppress successes
(e.g. in cron). Stdout carries only the delta JSONL.

`index_source` says where the document list came from: `live` (listing
crawled now), `stored-index-fresh` (crawled under 24 h ago, no listing
requests made), or `stored-index` (listing gated or unreachable; the run is
`partial`).

```json
{"connector": "dsn_mui", "run_id": 3, "status": "partial",
 "counts": {"discovered": 166, "new": 12, "changed": 1, "unchanged": 0,
            "failed": 2, "skipped_already_have": 151}}
```
