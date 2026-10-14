#!/usr/bin/env bash
# Verifies every CLI command before a push.
#
#   scripts/verify.sh          offline: exercises every command without touching the site
#   scripts/verify.sh --live   also runs `check` and a one-document sync (hits dsnmui.or.id
#                              and Google Drive; run it only when the site is not gated)
#
# Each command is checked against its expected exit code. Any failure fails the script.
set -u
cd "$(dirname "$0")/.."

LIVE=0
[ "${1:-}" = "--live" ] && LIVE=1
FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# run <allowed exit codes, space-separated> <description> <command...>
run() {
    local allowed=$1; shift
    local desc=$1; shift
    "$@" >"$TMP/out" 2>"$TMP/err"
    local got=$?
    case " $allowed " in
        *" $got "*) echo "PASS  $desc (exit $got)" ;;
        *)
            echo "FAIL  $desc (exit $got, expected: $allowed)"
            tail -n 3 "$TMP/err" | sed 's/^/        /'
            FAIL=1 ;;
    esac
}

skip() { echo "SKIP  $1"; }

index_fresh() {
    uv run python -c "import sys; from dsn_ingest import config; from dsn_ingest.store import Store; \
sys.exit(0 if Store().index_is_fresh(config.INDEX_MAX_AGE_S) else 1)"
}

echo "== offline =="
run 0 "modules compile"          uv run python -m py_compile dsn_ingest/*.py
run 0 "dsn --version"            uv run dsn --version
run 0 "dsn --help"               uv run dsn --help
run 0 "dsn help"                 uv run dsn help
for v in check discover sync list status; do
    run 0 "dsn help $v"          uv run dsn help "$v"
done
run 2 "unknown verb is rejected" uv run dsn nonsense
run 0 "dsn list"                 uv run dsn list
run 0 "dsn list --filter failed" uv run dsn list --filter failed
run "0 1" "dsn status (0 aligned, 1 not)" uv run dsn status
run "0 1" "dsn status emits valid JSON" bash -c 'uv run dsn status | python3 -c "import sys,json; json.load(sys.stdin)"'
run 0 "interface tests (offline, tests/fixtures)" uv run python -m pytest tests -q

if index_fresh; then
    run 0 "dsn sync --limit 0 (no downloads, no listing requests)" uv run dsn sync --limit 0 --quiet
else
    skip "dsn sync --limit 0: stored index is stale, a sync would crawl the listing (use --live)"
fi

if [ "$LIVE" -eq 1 ]; then
    echo "== live =="
    run 0 "dsn check (listing reachable and parseable)" uv run dsn check
    if index_fresh; then
        run "0 1" "dsn sync --limit 1 (one Drive download)" uv run dsn sync --limit 1 --quiet
    else
        skip "dsn sync --limit 1: stored index is stale (run dsn sync --refresh-index first)"
    fi
fi

echo
if [ "$FAIL" -eq 0 ]; then
    echo "all checks passed"
else
    echo "some checks failed"
fi
exit "$FAIL"
