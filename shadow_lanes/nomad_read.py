"""The inherited UI's SQL boundary: read-only transactions, bounded time, no legacy app import."""
import asyncio

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import psycopg2 as dialect

from . import source


def read(sql, params):
    db = None
    try:
        db = source.connect(readonly=True)
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL statement_timeout='8s'")
            compiled = text(sql).compile(dialect=dialect.dialect())
            values = {**compiled.params, **(params or {})}
            cur.execute(str(compiled), values)
            return [dict(row) for row in cur.fetchall()]
    except Exception:
        raise HTTPException(503, 'This retained dataset is unavailable after recovery; no historical values were reconstructed') from None
    finally:
        if db is not None:
            db.close()


async def fetch_all(sql, params=None):
    return await asyncio.to_thread(read, sql, params)
