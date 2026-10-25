"""Completion boundaries, including the actual PostgreSQL M6 predicate."""
import os
from datetime import datetime, timedelta

import psycopg2
import pytest

from model.session_clock import IST, available_at, available_at_sql, completed, timely_decision
from fusion.m6_select import LATEST_BAR_SQL


def at(day, hour, minute):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=IST)


@pytest.mark.parametrize("day,start,end", [
    ("2026-07-31", (15, 15), (15, 30)),
    ("2026-08-03", (15, 15), (15, 40)),
    ("2026-10-08", (15, 15), (15, 40)),
    ("2026-10-08", (14, 45), (15, 15)),
    ("2026-07-31", (9, 15), (9, 45)),
])
def test_availability_keeps_historical_and_normal_boundaries(day, start, end):
    stamp, expected = at(day, *start), at(day, *end)
    assert available_at(stamp) == expected
    assert not completed(stamp, expected-timedelta(seconds=1))
    assert completed(stamp, expected)


def test_final_derivative_prediction_cannot_be_prospective_before_close():
    stamp = at("2026-10-08", 15, 15)
    assert not timely_decision(stamp, at("2026-10-08", 15, 35))
    assert timely_decision(stamp, at("2026-10-08", 15, 42))
    assert not timely_decision(stamp, at("2026-10-08", 16, 10))


@pytest.mark.parametrize("day,before,close", [
    ("2026-07-31", (15, 29), (15, 30)),
    ("2026-08-03", (15, 39), (15, 40)),
    ("2026-10-08", (15, 35), (15, 40)),
])
def test_m6_postgres_cutoff_matches_python_without_table_writes(day, before, close):
    dsn = os.environ.get("VANGUARD_DATABASE_URL")
    if not dsn:
        pytest.skip("read-only PostgreSQL expression check needs VANGUARD_DATABASE_URL")
    stamp = at(day, 15, 15)
    # The CTE shadows the real timing table; only supplied constants are read.
    with psycopg2.connect(dsn) as connection:
        connection.set_session(readonly=True)
        with connection.cursor() as cursor:
            cursor.execute("WITH timing(ts) AS (VALUES (%(bar)s::timestamptz)) " + LATEST_BAR_SQL,
                           {"bar": stamp, "now": at(day, *before)})
            assert cursor.fetchone()[0] is None
            cursor.execute("WITH timing(ts) AS (VALUES (%(bar)s::timestamptz)) " + LATEST_BAR_SQL,
                           {"bar": stamp, "now": at(day, *close)})
            assert cursor.fetchone()[0] == stamp
            cursor.execute("SELECT " + available_at_sql("bar") +
                           " FROM (VALUES (%s::timestamptz)) q(bar)", (stamp,))
            assert cursor.fetchone()[0] == available_at(stamp)
