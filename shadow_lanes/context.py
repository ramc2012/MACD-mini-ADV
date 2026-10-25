"""Mirror already-collected public participant context; no download or app API dependency."""
import os
import psycopg2
from psycopg2.extras import execute_values


def sync_participants(destination):
    source = psycopg2.connect(host=os.environ.get("MI_PG_HOST", "nomadcurie_db"), user=os.environ["MI_PG_USER"],
        password=os.environ["MI_PG_PASSWORD"], dbname=os.environ.get("MI_PG_DB", "market_intelligence"), connect_timeout=5)
    source.set_session(readonly=True)
    try:
        with source.cursor() as cur:
            cur.execute("SET statement_timeout='5s'")
            cur.execute("SELECT day,participant,bucket,long_contracts,short_contracts FROM participant_oi WHERE day>=CURRENT_DATE-7")
            rows = cur.fetchall()
        if rows:
            with destination, destination.cursor() as cur:
                execute_values(cur, """INSERT INTO participant_oi(dt,participant,bucket,long_contracts,short_contracts)
                    VALUES %s ON CONFLICT(dt,participant,bucket) DO UPDATE SET
                    long_contracts=excluded.long_contracts,short_contracts=excluded.short_contracts""", rows)
        return len(rows)
    finally:
        source.close()
