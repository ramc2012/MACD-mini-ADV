#!/bin/sh
# Read-only crash gate. Never replace shared stores while application writers may be alive.
# SQLite structural recovery cannot establish preservation of paper books. A damaged store
# requires writer-isolated recovery and semantic validation before an operator clears it.
set -eu
DATA=$(dirname "${MACD_DATABASE_PATH:-/app/data/macd_trader.sqlite3}")
ROLE=${MACD_ENGINE_ROLE:-all}
MARK="$DATA/.clean_shutdown.$ROLE"
mkdir -p "$DATA/backups"
LOG="$DATA/backups/doctor.log"
log() { echo "$(date -u +%FT%TZ) [$ROLE] $*" | tee -a "$LOG"; }
if [ -f "$MARK" ]; then
  rm -f "$MARK"
  log "clean shutdown last time; integrity check skipped"
  exit 0
fi
command -v sqlite3 >/dev/null 2>&1 || { log "BLOCKED: sqlite3 missing; integrity unknown"; exit 1; }
log "unclean shutdown: checking the stores (read-only; no automatic replacement)"
RESULTS=$(mktemp -d)
trap 'rm -rf "$RESULTS"' EXIT HUP INT TERM
for f in "$DATA"/*.sqlite3; do
  [ -f "$f" ] || continue
  case "$(basename "$f")" in
    *.before-*|*.pre-*|*corrupt*|*.reindexed-*|*.recover*) continue ;;
  esac
  ( if sqlite3 "file:$f?mode=ro" "pragma quick_check" > "$RESULTS/$(basename "$f")" 2>&1; then
      :
    elif [ -f "$f-journal" ] || [ -f "$f-wal" ]; then
      # A write interrupted by a crash leaves a hot journal (or WAL) that only a writable open can roll back, so the
      # read-only check above cannot succeed. Verify a COPY opened normally (SQLite applies the journal to the copy);
      # the original stays untouched and the application's own open rolls it back the same way.
      # 21-22 Oct 2026: a hot journal on agentic_trader.sqlite3 after a power cut blocked the app 349 times.
      copy=$(mktemp -d)
      cp -p "$f" "$copy/db.sqlite3"
      [ ! -f "$f-journal" ] || cp -p "$f-journal" "$copy/db.sqlite3-journal"
      [ ! -f "$f-wal" ] || cp -p "$f-wal" "$copy/db.sqlite3-wal"
      if [ "$(sqlite3 "$copy/db.sqlite3" "pragma quick_check" 2>&1)" = "ok" ]; then
        echo "ok" > "$RESULTS/$(basename "$f")"
        echo "$(date -u +%FT%TZ) [$ROLE] $(basename "$f"): hot journal verified on a copy; the store rolls it back on open" >> "$LOG"
      else
        echo "hot journal present and the rolled-back copy failed its check" >> "$RESULTS/$(basename "$f")"
      fi
      rm -rf "$copy"
    else
      echo "check process failed" >> "$RESULTS/$(basename "$f")"
    fi ) &
done
wait
blocked=0
for result in "$RESULTS"/*; do
  [ -f "$result" ] || continue
  name=$(basename "$result")
  if [ "$(cat "$result")" = "ok" ]; then
    log "$name ok"
  else
    log "$name BLOCKED: integrity unknown or damaged; writer-isolated repair required; original untouched"
    blocked=1
  fi
done
[ "$blocked" -eq 0 ] || exit 1
log "check done"
