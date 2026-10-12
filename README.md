# DSN-MUI Source Connector

Fetches the fatwa corpus from [dsnmui.or.id](https://dsnmui.or.id/kategori/fatwa/)
(≈166 fatwas, table listing, PDFs hosted on Google Drive) into structured raw
data: archived bytes + extracted text + SQLite state. No LLM, no reasoning —
retrieval only. See `CONTRACT.md` for how this plugs into the multi-source
orchestrator (OJK / BI connectors live in their own repos, same contract),
and `ARCHITECTURE.md` for how it's built and why.

## Quickstart

Everything runs through [uv](https://docs.astral.sh/uv/) — clone and go:

```bash
uv run dsn check                    # live: reachability + parse sanity
uv run dsn sync --limit 3           # live: first 3 docs (smoke test)
uv run dsn sync                     # live: full corpus, incremental
uv run dsn list --filter failed,missing
uv run dsn status                   # coverage: remote vs local, errors, runs
```

`uv run dsn help` shows every verb and the exit codes; `uv run dsn help sync`
shows the options for one verb.

First `uv run` creates the environment automatically (`uv.lock` pins it).
Plain Python works too: `pip install -e . && python -m dsn_ingest …`.

No offline/mock mode: all verification runs against the live site — be
moderate (1s politeness delay between downloads; `--limit` for smoke tests).
`status` and `list` are fully local, they never touch the network.

`sync` reuses the stored listing for 24 h, so routine runs make no listing
requests. Pass `--refresh-index` to re-crawl the listing on demand.

Verbs: `check` · `discover` · `sync` (`--all` = re-download everything for
byte-level change detection; default skips what we already hold) ·
`list` (full local snapshot) · `status` (alignment + monitoring).
Exit codes: 0 ok · 1 partial · 2 run-level failure.

## Before pushing

`scripts/verify.sh` runs every CLI command against its expected exit code.
It is offline by default, so it never touches the site. `scripts/verify.sh --live`
adds the commands that do: `check` and a one-document sync.

The repo's `.githooks/pre-push` runs the offline suite on every push. It is
enabled with `git config core.hooksPath .githooks`; run that once per clone.

## Layout

```
pyproject.toml       uv env + `dsn` CLI entry point
CONTRACT.md          the inter-repo contract (share this with OJK/BI connectors)
dsn_ingest/
  __main__.py        CLI: check · discover · sync · list · status
  base.py            contract: verbs, error classes, delta semantics
  connector.py       the five modules wired together
  parser.py          listing table → FatwaEntry
  fetcher.py         requests + fallback ladder (transient/quota/auth/content/not_found)
  store.py           SQLite state + content-addressed blob store
  extract.py         PDF → text (PyMuPDF; flags needs_ocr)
  models.py          FatwaEntry (internal) + DocRecord (emitted pointers)
  config.py          URLs + tunables
data/dsn/            runtime output: blobs/, text/, dsn_fatwa.db (gitignored)
```

## Facts learned from the site (Oct 2026)

- Plain HTTP mostly works; the site intermittently serves a JS bot-interstitial
  ("One moment, please…", volume-triggered). Handled by waits + retries in
  `get_html`; if it persists, `sync` degrades gracefully to the stored index
  — Drive downloads don't go through dsnmui.or.id, so acquisition continues.
- Listing is one table per page: `Nomor | Tentang | Drive link`. No detail pages.
- **Keep `resourcekey`** in Drive hrefs — legacy files (≤2015) demand it and
  answer with a sign-in page without it.
- Latin text is in the PDF text layer for every fatwa. Arabic is not, for most of the corpus: 2000–2007 fatwas store it as presentation forms (recovered by NFKC) and 2008+ fatwas have it only inside page images, so it needs OCR.
- Fatwa PDFs are not all born-digital. Modern ones are scanned pages with a Latin text layer.
- Pagination is `/page/N/`, 10 rows/page; `discover` stops on the first
  404/empty page, so new fatwas are picked up automatically next run.
- 2 of 166 rows (fatwas 26/2002, 68/2008) have broken upstream links
  (`href="https://"`) — they are indexed and reported as `content` failures,
  never silently dropped.
