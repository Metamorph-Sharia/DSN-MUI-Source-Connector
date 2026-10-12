# Architecture — DSN-MUI Source Connector

v0.3.1 · October 2026. This document explains how the connector is built and
*why*; `CONTRACT.md` defines what it promises to the outside world, and
`README.md` covers day-to-day usage.

---

## 1. Overview

A single-process CLI pipeline that

1. crawls the fatwa listing on dsnmui.or.id (HTML tables, paginated),
2. downloads the PDFs (hosted on Google Drive),
3. extracts a normalized raw text (`PyMuPDF`),
4. dedupes and detects changes by content hash (sha256),
5. keeps full operational state (runs, errors, coverage) in SQLite,

and exposes everything as **JSONL pointer records + exit codes** so an
orchestrator can run it alongside sibling connectors (OJK, BI) that live in
their own repos with no shared code.

**Goals**: automated, resumable, polite retrieval of structured raw data.
**Non-goals (now)**: classification/relevance of documents (future LLM step
over the exported corpus), OCR (downstream job over archived blobs),
distributed scheduling, mid-run checkpointing.

### Layering

```
┌────────────────────────────────────────────────────────────┐
│ __main__.py   CLI verbs: check · discover · sync · list · status │  ← contract surface
├────────────────────────────────────────────────────────────┤
│ connector.py  DsnConnector: orchestration, failure policy,│  ← the only module that
│               delta emission, coverage reporting            │    knows the others exist
├──────────────┬──────────────────┬──────────────────────────┤
│ fetcher.py   │ parser.py        │ extract.py               │  ← stateless workers:
│ transport +  │ table HTML →     │ PDF → text + needs_ocr   │    network / HTML / PDF
│ failure      │ FatwaEntry       │ (PyMuPDF)                │    only, never each other
│ ladder       │                  │                          │
├──────────────┴──────────────────┴──────────────────────────┤
│ store.py    SQLite state (index, docs, runs, errors) +     │
│             content-addressed blob/text files              │
├────────────────────────────────────────────────────────────┤
│ config.py   URLs & tunables    models.py  FatwaEntry · DocRecord │
│ base.py     error vocabulary + SourceConnector Protocol (documentation-grade) │
└────────────────────────────────────────────────────────────┘
```

Everything below `connector.py` is deliberately dumb and independent: the
parser never touches the network, the store never knows what a fatwa site
looks like, the fetcher never hashes. This is what lets another source
(OJK/BI) reuse the shape by swapping the three leaf modules.

---

## 2. The five modules → five verbs

| Module (plan)         | Where                          | Behavior |
|-----------------------|--------------------------------|----------|
| 1. Source discovery   | `discover`                     | walk `/kategori/fatwa/page/N/`; parse `<table>` rows → `FatwaEntry`; upsert `fatwa_index`. Termination is deterministic: first page that 404s or yields zero rows ends the crawl. |
| 2. Acquisition        | `sync` → `_acquire`            | convert Drive *view* href → download URL **preserving `resourcekey` verbatim**; bytes through the failure ladder (§5). |
| 3. Extraction/normalization | `sync` → `extract.py`    | PyMuPDF text layer → `data/dsn/text/<sha>.txt`. Text shorter than `MIN_TEXT_LEN` (200) → `needs_ocr: true`, no text written. Non-PDF or missing engine → `needs_ocr: null` (undecided). |
| 4. Dedup/change detection | `sync` (sha compare in `store.record_success`) | sha256 = version identity → `new` / `changed` / `unchanged`. Also detects *link* changes (listing href moved → re-acquire). |
| 5. Monitoring         | `check`, `status`              | `run` + `run_error` tables; coverage reconciliation; error breakdown by class; last-runs feed. `status`/`list` never touch the network. |

---

## 3. Data flow

```
                     ┌────────────┐
 listing pages ────▶│  parser.py │──▶ FatwaEntry ────▶ fatwa_index (SQLite)
                     └────────────┘
                                        │ sync selects todo (incremental, §6)
                                        ▼
                     ┌────────────┐
 Drive href ────────▶│ fetcher.py │──▶ bytes ──▶ sha256 ──▶ blobs/ab/<sha>.pdf
 (resourcekey kept)  └────────────┘                  │
                                                     ▼
                                          PyMuPDF text ──▶ text/<sha>.txt
                                                     │
                                                     ▼
                       fatwa_docs row (result, error class, paths)
                                                     │
                          delta JSONL (new|changed|failed) ──▶ stdout/--out
                          snapshot (list) / coverage (status) ──▶ stdout
```

Two output styles, same state:

- **`sync` = delta.** Emits one JSONL line per `new`, `changed`, `failed`
  document only; run report (counts, `index_source`) on **stderr**; exit
  code 0/1/2. For scheduled feeding of a downstream corpus file.
- **`list` / `status` = snapshot.** Every indexed item with status
  `unchanged | failed | missing` (missing = discovered remotely, never
  acquired). `status` adds reconciliation: `remote_indexed` vs
  `never_acquired` vs `failed_by_error_class`, plus recent runs.

Records are **pointers, not payloads**: `raw_path` / `text_path` refer to
files on disk; the JSONL never inlines document bytes or text. The field
set is intentionally not a frozen schema until all three connectors have
emitted real records (§9).

### State model (`store.py`)

```
fatwa_index  source_id PK, number, title, pdf_href, year, month, first_seen, last_seen
fatwa_docs   source_id PK, sha256, size, content_type, raw_path, text_path,
             needs_ocr (NULL=undecided), pdf_href (used for current sha),
             last_result, error_class, error_message, fetched_at, updated_at
blobs        sha256 PK, path, size, mime, ref_count, created
run          run_id PK, kind, started/finished, status, counts_json
run_error    run_id, source_id, stage, kind, message, at
```

Files are content-addressed: `blobs/<sha[0:2]>/<sha>.pdf`,
`text/<sha>.txt`. Same bytes → same path, writes are no-ops, versions are
**never deleted** (a failed re-acquisition keeps the last good copy).
Change detection = "sha differs from `fatwa_docs.sha256`".

---

## 4. Site-specific knowledge (baked into code, verified live Oct 2026)

| Fact | Consequence |
|---|---|
| Listing is one HTML `<table>` per page: `Nomor ‖ Tentang ‖ Drive link` (10 rows × 17 pages = **166 fatwas**) | parser is table-row based; no detail pages; `source_id` = sanitized number (`162-DSN-MUI-VII-2025`) |
| Two rows (fatwas 26/2002, 68/2008) have broken upstream `href="https://"` | still indexed; fail at fetch as `content`; visible in `status`, auto-retried forever — never silently dropped |
| PDFs live on Google Drive, **not** dsnmui.or.id | acquisition survives dsnmui downtime/gating (basis for degraded mode, §5) |
| Legacy files (≤ ~2015, ids starting `0BxT…`) require the `resourcekey` query param | `drive_download_url()` copies resourcekey from the listing href into the download URL; stripping it yields a sign-in page (`auth` error, proven live) |
| Drive >~25 MB answers a "can't scan for viruses" form (hidden `id`+`uuid` inputs) | auto-detected and resubmitted once (`_consent_form`) |
| dsnmui.or.id intermittently serves a JS bot-interstitial ("One moment, please…", volume-triggered, minutes-hours) | `get_html` waits 20/40/60 s; then raises `gate` — not fatal (§5) |
| Fatwa PDFs are born-digital (typed, not scanned) | text extraction works, no OCR needed in this corpus; `needs_ocr` stays as the general flag for other connectors |

---

## 5. Failure model

**Cardinal rule: one bad document never kills a run.**

### Per-request ladder (`fetcher.py._get` / `get_file`), in order

1. timeout / connection error / 5xx → retry ×3, exponential backoff (2/4/8 s) → `transient`
2. HTTP 429 → respect `Retry-After`, backoff, then → `quota`
3. HTTP 403/404 → `not_found` (also the normal end-of-pagination signal)
4. Drive URL conversion (§4) → GET; redirect landed on `accounts.google.com` → `auth`
5. got HTML instead of file + sign-in markers → `auth`
6. got HTML + virus-scan consent form (`id`+`uuid`) → resubmit form once
7. still not the expected file → `content`; unusable href (e.g. `https://`) → `content`
8. anything unforeseen → `unknown`

`get_html` wraps the same ladder with gate-waits (20/40/60 s) → `gate`.

### Per-run degradation (`connector.sync`)

If **discovery** fails (gate/quota/auth at listing level), `sync` falls back
to the **stored index** and keeps acquiring: downloads depend only on
Google Drive. The run reports `status: "partial"`, `index_source:
"stored-index"`, and the discover failure is logged. Verified live while the
site was gated: 2 legacy fatwas acquired without touching dsnmui.or.id.

### Retry & retention policy

- Failed items are auto-retried by the *next default sync*: incremental
  selection skips only docs whose `last_result` is a success — failures are
  permanently in the to-do set.
- Successes keep prior versions in blobs; nothing is ever deleted, so a bad
  upstream moment cannot lose corpus data.
- Errors accumulate in two places: per-document (`fatwa_docs.error_*`) and
  per-run event log (`run_error`), both surfaced by `status`.

---

## 6. Sync selection logic (incremental by default)

```
for each indexed entry:
    have  = doc exists AND last_result ∈ {new, changed, unchanged}
    moved = listing pdf_href changed since last acquisition   ← linkrot/new file
    skip if have AND NOT moved AND NOT --all
→ default sync = "acquire what's missing or failed or moved"  (cheap, resumable)
→ --all        = re-download everything, sha-compare for byte-level change
                 detection (166 docs ≈ 1 GB; run occasionally, not on a timer)
```

Byte-level "the PDF changed silently" detection therefore costs a full pass
by design — Drive offers no usable ETag/Last-Modified. `--limit N` caps any
run for smoke tests.

---

## 7. Interface & packaging

- **CLI is the contract.** `pyproject.toml [project.scripts]` → `dsn`.
  Orchestrator calls: `uv run --directory <connector-repo> dsn sync --out …`
  per repo; merges JSONL. Zero shared code; language-per-repo is free.
- **`base.SourceConnector` Protocol** is documentation-grade duck typing:
  the same five methods, no import requirement for OJK/BI repos.
- **uv environment** (`uv.lock`): clone-and-go for the friend; no binary
  packaging (the repo is read/modified by other humans — that beats a
  frozen bundle at this stage).
- **No offline/mock mode**: fixtures drifted from real markup twice in one
  day during development; a stale mock can pass while live is broken.
  `check` + `sync --limit 3` against the live site is the regression suite;
  `status`/`list` remain network-free.
- **Exit codes**: 0 ok · 1 partial (some docs failed / degraded index /
  not aligned) · 2 run-level failure. Stdout is always machine-read
  (JSON/JSONL); human noise goes to stderr.

---

## 8. Known limitations (deliberate at this stage)

1. `fatwa_docs.raw_path` stores **absolute paths** — moving the repo folder
   breaks state (it happened; repaired). Fix = store paths relative to
   `PROJECT_ROOT` if the repo ever moves again.
2. No concurrency — single-threaded polite crawl (~1 s/file, ~30 min full
   pass). 166 docs don't justify parallelism's failure-classification mess.
3. Blob GC is counting (`ref_count`) without collection — fine at this
   corpus scale.
4. Gate handling waits 2 minutes before degrading; if the site ever gates
   *hard* permanently, the stored-index path covers existing docs but new
   fatwas need the gate solved (browser-cookie extraction or an official
   API/feed — revisit before submission week).
5. `needs_ocr` heuristic (length < 200) can mislabel genuinely short
   documents; acceptable here (fatwas are 10+ pages).

## 9. Open design decisions (parked, with owners)

- **Record schema**: pointer fields listed in CONTRACT.md; freeze only
  after OJK/BI emit real records and the orchestrator defines consumption.
- **Where `sync` cadence lives**: cron on each repo vs a thin orchestrator
  runner — unresolved, doesn't block building.
- **OCR**: separate downstream job consuming `needs_ocr: true` blobs;
  engine choice is the friend's problem until scanned docs actually arrive.
