#!/bin/sh
# Back up and recover the three databases damaged by the 15 Oct 12:53 crash. Runs with the engine and desk stopped.
set -e
apk add -q sqlite >/dev/null 2>&1
D=/app/data; B=$D/backups/crash-20261015
mkdir -p $B
for db in mp_trader ticks historical; do
  echo "$(date +%T) backup $db"
  cp -p $D/$db.sqlite3 $B/$db.sqlite3
  [ -f $D/$db.sqlite3-wal ] && cp -p $D/$db.sqlite3-wal $B/ || true
done
for db in mp_trader ticks historical; do
  echo "$(date +%T) recover $db"
  rm -f $D/$db.recovered.sqlite3
  sqlite3 $B/$db.sqlite3 ".recover --ignore-freelist" | sqlite3 $D/$db.recovered.sqlite3
  echo "$(date +%T) check $db: $(sqlite3 $D/$db.recovered.sqlite3 'pragma quick_check' | head -3)"
done
echo "$(date +%T) done"
