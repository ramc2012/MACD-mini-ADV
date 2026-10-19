#!/bin/sh
# historical.sqlite3 only; the backup copy was taken by recover.sh. No set -e: the importer's harmless
# "sqlite_sequence reserved" parse error returns non-zero.
apk add -q sqlite >/dev/null 2>&1
D=/app/data; B=$D/backups/crash-20261015
rm -f $D/historical.recovered.sqlite3
echo "$(date +%T) recover historical"
sqlite3 $B/historical.sqlite3 ".recover --ignore-freelist" | sqlite3 $D/historical.recovered.sqlite3 2>&1 | grep -v sqlite_sequence
echo "$(date +%T) check: $(sqlite3 $D/historical.recovered.sqlite3 'pragma quick_check' | head -3)"
echo "$(date +%T) done"
