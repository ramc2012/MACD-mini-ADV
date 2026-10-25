"""Train the two shadow rankers for the pre-close 1-3 session swing lane.

Decision features stop at the 14:15 IST bar (available at 14:45).  Historical
entry is the 14:45 bar close (available at 15:15), so the label includes the
overnight move.  Direction and exact option payoff are deliberately separate:
the first ranks symbol/side opportunities; the second ranks ATM and liquid
25-delta expressions.  Neither model can activate a ticket or broker order.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model.return_calibration import calibrate_returns
from model.listwise_ranker import fit_listwise_mlp, ranking_metrics  # noqa: E402
from model.nonlinear_selector import FEATURE_NAMES, feature_row  # noqa: E402
from research.label_availability import purged_chronological_split, require_usable_partitions  # noqa: E402

DEFAULT_DSN = ""
DIRECTION_FEATURES = FEATURE_NAMES + ("horizon_2", "horizon_3")
CONTRACT_FEATURES = FEATURE_NAMES + ("horizon_2", "horizon_3", "contract_is_wing",)
IST = ZoneInfo("Asia/Kolkata")

BASE_SQL = r"""
WITH session_calendar AS MATERIALIZED (
    SELECT session_date,lead(session_date,1) OVER (ORDER BY session_date) AS day_1,
           lead(session_date,2) OVER (ORDER BY session_date) AS day_2,
           lead(session_date,3) OVER (ORDER BY session_date) AS day_3
    FROM (SELECT DISTINCT (time AT TIME ZONE 'Asia/Kolkata')::date session_date
          FROM underlying_spot_candles WHERE interval='30minute') d
), candidates AS MATERIALIZED (
    SELECT ce.*,sc.day_1,sc.day_2,sc.day_3
    FROM candidate_evaluations ce JOIN session_calendar sc
      ON sc.session_date=(ce.ts AT TIME ZONE 'Asia/Kolkata')::date
    WHERE (ce.ts AT TIME ZONE 'Asia/Kolkata')::time=time '14:15'
      AND sc.day_3 IS NOT NULL
), bounds AS MATERIALIZED (
    SELECT min(ts)-interval '1 day' lo,
           ((max(day_3)+1)::date::timestamp AT TIME ZONE 'Asia/Kolkata') hi
    FROM candidates
), spot_marks AS MATERIALIZED (
    SELECT DISTINCT ON (s.underlying,s.time) s.underlying,s.time,s.close,s.synced_at
    FROM underlying_spot_candles s,bounds b
    WHERE s.interval='30minute' AND s.time>=b.lo AND s.time<b.hi
      AND (s.time AT TIME ZONE 'Asia/Kolkata')::time IN (time '14:15',time '14:45')
    ORDER BY s.underlying,s.time,
             CASE s.source WHEN 'upstox_spot' THEN 0 WHEN 'upstox_sweep' THEN 1
                           WHEN 'upstox' THEN 2 WHEN 'fyers_spot' THEN 3 ELSE 9 END,
             s.synced_at DESC
)
SELECT ce.*,
       source_spot.close::double precision source_spot,
       entry_spot.close::double precision entry_spot,
       day_1_spot.close::double precision day_1_spot,
       day_2_spot.close::double precision day_2_spot,
       day_3_spot.close::double precision day_3_spot,
       source_spot.synced_at source_spot_available_at,
       entry_spot.synced_at entry_spot_available_at,
       day_1_spot.synced_at day_1_spot_available_at,
       day_2_spot.synced_at day_2_spot_available_at,
       day_3_spot.synced_at day_3_spot_available_at
FROM candidates ce
JOIN spot_marks source_spot ON source_spot.underlying=ce.symbol AND source_spot.time=ce.ts
JOIN spot_marks entry_spot ON entry_spot.underlying=ce.symbol
  AND entry_spot.time=(((ce.ts AT TIME ZONE 'Asia/Kolkata')::date+time '14:45') AT TIME ZONE 'Asia/Kolkata')
JOIN spot_marks day_1_spot ON day_1_spot.underlying=ce.symbol
  AND day_1_spot.time=((ce.day_1+time '14:45') AT TIME ZONE 'Asia/Kolkata')
JOIN spot_marks day_2_spot ON day_2_spot.underlying=ce.symbol
  AND day_2_spot.time=((ce.day_2+time '14:45') AT TIME ZONE 'Asia/Kolkata')
JOIN spot_marks day_3_spot ON day_3_spot.underlying=ce.symbol
  AND day_3_spot.time=((ce.day_3+time '14:45') AT TIME ZONE 'Asia/Kolkata')
WHERE entry_spot.close>0
ORDER BY ce.ts,ce.symbol
"""

SOURCE_OPTIONS_SQL = r"""
SELECT DISTINCT ON (o.underlying,o.expiry,o.strike,o.option_type)
       o.underlying,o.expiry,o.strike::double precision strike,o.option_type,
       o.close::double precision premium,o.open::double precision option_open,
       o.high::double precision option_high,o.low::double precision option_low,
       o.volume::double precision option_volume,o.oi::double precision option_oi,
       o.iv::double precision option_iv,o.delta::double precision option_delta,
       o.gamma::double precision option_gamma,o.theta::double precision option_theta,
       o.vega::double precision option_vega,o.instrument_key,o.source option_source,
       o.synced_at source_option_available_at,
       pr.straddle_to_spot,pr.normalized_straddle,pr.strangle_straddle_ratio,
       pr.put_wing_iv_ratio,pr.call_wing_iv_ratio,pr.atm_put_call_premium_ratio,
       pr.atm_call_put_extrinsic_ratio,pr.premium_pcr,
       pr.call_itm_atm_extrinsic_ratio,pr.call_otm_atm_extrinsic_ratio,
       pr.put_itm_atm_extrinsic_ratio,pr.put_otm_atm_extrinsic_ratio,
       pr.n_strikes ratio_n_strikes
FROM option_premium_candles o
LEFT JOIN option_premium_ratios pr
  ON pr.ts=o.time AND pr.symbol=o.underlying AND pr.expiry=o.expiry
WHERE o.time=%s AND o.underlying=ANY(%s) AND o.interval='30minute'
  AND o.option_type IN ('CE','PE') AND o.close>=5 AND o.expiry>=%s
ORDER BY o.underlying,o.expiry,o.strike,o.option_type,
         (o.source='upstox') DESC,o.source,o.synced_at DESC
"""

MARKS_SQL = r"""
WITH wanted(source_ts,symbol,option_type,expiry,strike,entry_time,day_1_time,day_2_time,day_3_time) AS
     (VALUES %s)
SELECT w.source_ts,w.symbol,w.option_type,w.expiry,w.strike,
       entry_mark.close::double precision entry_option,
       day_1_mark.close::double precision day_1_option,
       day_2_mark.close::double precision day_2_option,
       day_3_mark.close::double precision day_3_option,
       entry_mark.synced_at entry_available_at,
       day_1_mark.synced_at day_1_available_at,
       day_2_mark.synced_at day_2_available_at,
       day_3_mark.synced_at day_3_available_at
FROM wanted w
JOIN LATERAL (
    SELECT o.close,o.synced_at FROM option_premium_candles o
    WHERE o.underlying=w.symbol AND o.option_type=w.option_type
      AND o.expiry=w.expiry AND o.strike=w.strike AND o.interval='30minute'
      AND o.time=w.entry_time
    ORDER BY (o.source='upstox') DESC,o.source,o.synced_at DESC LIMIT 1
) entry_mark ON true
JOIN LATERAL (
    SELECT o.close,o.synced_at FROM option_premium_candles o
    WHERE o.underlying=w.symbol AND o.option_type=w.option_type
      AND o.expiry=w.expiry AND o.strike=w.strike AND o.interval='30minute'
      AND o.time=w.day_1_time
    ORDER BY (o.source='upstox') DESC,o.source,o.synced_at DESC LIMIT 1
) day_1_mark ON true
JOIN LATERAL (
    SELECT o.close,o.synced_at FROM option_premium_candles o
    WHERE o.underlying=w.symbol AND o.option_type=w.option_type
      AND o.expiry=w.expiry AND o.strike=w.strike AND o.interval='30minute'
      AND o.time=w.day_2_time
    ORDER BY (o.source='upstox') DESC,o.source,o.synced_at DESC LIMIT 1
) day_2_mark ON true
JOIN LATERAL (
    SELECT o.close,o.synced_at FROM option_premium_candles o
    WHERE o.underlying=w.symbol AND o.option_type=w.option_type
      AND o.expiry=w.expiry AND o.strike=w.strike AND o.interval='30minute'
      AND o.time=w.day_3_time
    ORDER BY (o.source='upstox') DESC,o.source,o.synced_at DESC LIMIT 1
) day_3_mark ON true
"""

def _instrument(row: dict) -> dict:
    return {
        "premium": row["premium"], "spot": row["source_spot"], "strike": row["strike"],
        "open": row["option_open"], "high": row["option_high"], "low": row["option_low"],
        "volume": row["option_volume"], "oi": row["option_oi"], "iv": row["option_iv"],
        "delta": row["option_delta"], "gamma": row["option_gamma"],
        "theta": row["option_theta"], "vega": row["option_vega"],
        "dte_days": (row["expiry"] - row["ts"].date()).days,
        **{name: row.get(name) for name in (
            "straddle_to_spot", "normalized_straddle", "strangle_straddle_ratio",
            "put_wing_iv_ratio", "call_wing_iv_ratio", "atm_put_call_premium_ratio",
            "atm_call_put_extrinsic_ratio", "premium_pcr",
            "call_itm_atm_extrinsic_ratio", "call_otm_atm_extrinsic_ratio",
            "put_itm_atm_extrinsic_ratio", "put_otm_atm_extrinsic_ratio",
            "ratio_n_strikes",
        )},
    }


def load_examples(connection, cost_pct: float = 0.01) -> dict[str, tuple]:
    print("extracting pre-close ATM/25-delta labels ...", flush=True)
    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(BASE_SQL)
        base_rows = cursor.fetchall()
    by_ts: dict = {}
    for row in base_rows:
        by_ts.setdefault(row["ts"], []).append(dict(row))
    contracts = []
    for number, (source_ts, session_rows) in enumerate(sorted(by_ts.items()), start=1):
        by_symbol = {row["symbol"]: row for row in session_rows}
        # A D+3 label must remain on a live contract through the D+3 mark.
        # Using D+2 here admitted expiry-week contracts that could not carry
        # the requested three-session holding period and biased the cohort.
        min_expiry = max(row["day_3"] for row in session_rows)
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(SOURCE_OPTIONS_SQL, (source_ts, list(by_symbol), min_expiry))
            option_rows = cursor.fetchall()
        options_by_side: dict = {}
        for option in option_rows:
            options_by_side.setdefault((option["underlying"], option["option_type"]), []).append(dict(option))
        for (symbol, side), choices in options_by_side.items():
            base_row = by_symbol.get(symbol)
            if base_row is None:
                continue
            front_expiry = min(row["expiry"] for row in choices)
            front = [row for row in choices if row["expiry"] == front_expiry]
            atm = min(front, key=lambda row: (abs(row["strike"] - base_row["source_spot"]),
                                              abs(abs(row["option_delta"] or 0.5) - 0.5), row["strike"]))
            selected = [(atm, "ATM")]
            wings = [row for row in front if row["option_delta"] is not None
                     and abs(abs(row["option_delta"]) - 0.25) <= 0.12]
            if wings:
                wing = min(wings, key=lambda row: (abs(abs(row["option_delta"]) - 0.25), row["strike"]))
                if wing["strike"] != atm["strike"]:
                    selected.append((wing, "WING_25D"))
            for option, kind in selected:
                contracts.append({**base_row, **option, "contract_kind": kind})
        if number % 10 == 0:
            print(f"  source sessions {number}/{len(by_ts)}; candidates={len(contracts):,}", flush=True)

    mark_lookup = {}
    wanted = []
    for row in contracts:
        source_session = row["ts"].astimezone(IST).date()
        wanted.append((
            row["ts"], row["symbol"], row["option_type"], row["expiry"], row["strike"],
            datetime.combine(source_session, time(14, 45), IST).astimezone(UTC),
            datetime.combine(row["day_1"], time(14, 45), IST).astimezone(UTC),
            datetime.combine(row["day_2"], time(14, 45), IST).astimezone(UTC),
            datetime.combine(row["day_3"], time(14, 45), IST).astimezone(UTC),
        ))
    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        for start in range(0, len(wanted), 1000):
            rows = psycopg2.extras.execute_values(
                cursor, MARKS_SQL, wanted[start:start + 1000], page_size=1000, fetch=True)
            for row in rows:
                key = (row["source_ts"], row["symbol"], row["option_type"],
                       row["expiry"], float(row["strike"]))
                mark_lookup[key] = dict(row)
    rows = []
    for row in contracts:
        key = (row["ts"], row["symbol"], row["option_type"], row["expiry"], float(row["strike"]))
        marks = mark_lookup.get(key)
        if marks and marks["entry_option"] and marks["entry_option"] > 0:
            rows.append({**row, **marks})
    print(f"  complete exact-contract paths={len(rows):,}", flush=True)
    direction, contract = ([], [], [], [], [], []), ([], [], [], [], [], [])
    seen_direction = set()
    for row in rows:
        session = (row["ts"].astimezone(IST).date() if row["ts"].tzinfo else row["ts"].date())
        base = feature_row(dict(row), _instrument(row), row["option_type"], row["ts"])
        sign = 1.0 if row["option_type"] == "CE" else -1.0
        for horizon in (1, 2, 3):
            label_end = datetime.combine(row[f"day_{horizon}"], time(15, 15), IST).astimezone(UTC)
            # synced_at is latest-known archive provenance, not guaranteed first
            # ingestion. Using it conservatively can refuse training; silently
            # treating reconstructed labels/features as available earlier cannot.
            timings = [row.get("computed_at"), row.get("source_option_available_at"),
                       row.get("source_spot_available_at"), row.get("entry_available_at"),
                       row.get(f"day_{horizon}_available_at")]
            available_at = max([label_end, *timings]) if all(timings) else None
            spot_timings = [*timings, row.get("entry_spot_available_at"),
                            row.get(f"day_{horizon}_spot_available_at")]
            direction_available_at = max([label_end, *spot_timings]) if all(spot_timings) else None
            spot_exit = row[f"day_{horizon}_spot"]
            directional_target = float(np.clip(sign * (spot_exit / row["entry_spot"] - 1.0), -0.20, 0.20))
            direction_key = (row["ts"], row["symbol"], row["option_type"], horizon)
            if row["contract_kind"] == "ATM" and direction_key not in seen_direction:
                direction[0].append(np.append(base, [float(horizon == 2), float(horizon == 3)]))
                direction[1].append(directional_target)
                direction[2].append(session)
                direction[3].append(f"{row['ts'].isoformat()}|{row['symbol']}|{row['option_type']}|h{horizon}")
                direction[4].append(label_end)
                direction[5].append(direction_available_at)
                seen_direction.add(direction_key)
            option_exit = row[f"day_{horizon}_option"]
            option_target = float(np.clip(option_exit / row["entry_option"] - 1.0 - cost_pct, -0.90, 4.0))
            contract[0].append(np.append(base, [float(horizon == 2), float(horizon == 3),
                                                 float(row["contract_kind"] == "WING_25D")]))
            contract[1].append(option_target)
            contract[2].append(session)
            contract[3].append(
                f"{row['ts'].isoformat()}|{row['symbol']}|{row['option_type']}|"
                f"{row['expiry']}|{row['strike']}|{row['contract_kind']}|h{horizon}")
            contract[4].append(label_end)
            contract[5].append(available_at)
    if not direction[0] or not contract[0]:
        raise RuntimeError("no pre-close ranker examples were formed")
    return {
        "direction": (np.vstack(direction[0]), np.asarray(direction[1]), *direction[2:]),
        "contract": (np.vstack(contract[0]), np.asarray(contract[1]), *contract[2:]),
    }


def train_role(connection, role: str, dataset: tuple, feature_names: tuple[str, ...],
               epochs: int, write: bool) -> dict:
    x, y, sessions, identities, label_ends, available_ats = dataset
    decisions = [datetime.fromisoformat(identity.split('|', 1)[0]) + timedelta(minutes=30)
                 for identity in identities]
    train, validation, test, train_days, validation_days, test_days = require_usable_partitions(
        purged_chronological_split(sessions, label_ends, available_ats, decisions))
    # The product is top-10 CE AND top-10 PE.  Train each side/horizon as its
    # own ranking group; a mixed group lets the stronger side crowd the other
    # side out of the loss and then asks selection to recover two leaderboards.
    def group_key(day, identity):
        parts = identity.split('|')
        side = next((part for part in parts if part in ("CE", "PE")), "UNKNOWN")
        return f"{day}|{side}|{parts[-1]}"
    groups = np.asarray([group_key(day, identity) for day, identity in zip(sessions, identities)])
    model, fit = fit_listwise_mlp(
        x[train], y[train], groups[train], x[validation], y[validation], groups[validation],
        feature_names, epochs=epochs, seed=20260904 if role == "direction" else 20260905,
    )
    validation_metrics = ranking_metrics(y[validation], model.score(x[validation]), groups[validation])
    test_metrics = ranking_metrics(y[test], model.score(x[test]), groups[test])
    dataset_hash = hashlib.sha256(
        np.ascontiguousarray(x).tobytes() + np.ascontiguousarray(y).tobytes()
        + "\n".join(identities).encode()
    ).hexdigest()
    gate = bool(
        (test_metrics["overlap_at_10"] or 0.0) >= 2.0
        and (test_metrics["selected_mean"] or 0.0) > 0.0
        and (test_metrics["positive_group_rate"] or 0.0) >= 0.50
    )
    version = f"listwise_preclose_{role}_v2_{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    model.version, model.role, model.status = version, role, "shadow"
    if role == "contract":
        # Validation calibration is experimental. The test slice is never fit.
        h = np.asarray([int(identity.rsplit('|h', 1)[-1]) for identity in identities])
        model.return_calibration = calibrate_returns(
            model.score(x[validation]), y[validation],
            np.asarray(sessions)[validation], h[validation])
    artifact = model.to_artifact()
    artifact_hash = hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    metrics = {
        **fit, "validation": validation_metrics, "test": test_metrics,
        "historical_gate_passed": gate,
        "gate": "test overlap@10>=2, selected mean>0, positive groups>=50%",
        "activation": "shadow watchlist only; no ticket or broker path",
        "target": (
            "side-adjusted underlying return from source-session 14:45 close to D+1/D+2/D+3 14:45 close"
            if role == "direction" else
            "exact ATM/25-delta option net return from source-session 14:45 close to D+1/D+2/D+3 14:45 close"
        ),
        "overnight_included": True, "cost_pct": 0.01 if role == "contract" else 0.0,
        "split_policy": "purge actual label end and conservative recorded availability before next partition decision",
        "purged_examples": int(len(y) - train.sum() - validation.sum() - test.sum()),
        "ranking_group": "source session x CE/PE side x horizon",
    }
    card = {"version": version, "role": role, "status": "shadow", "metrics": metrics}
    print(json.dumps(card, indent=2), flush=True)
    if write:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO vanguard_rank_model_versions
                       (version,role,family,status,feature_names,artifact,artifact_sha256,dataset_sha256,
                        training_start,training_end,validation_start,validation_end,test_start,test_end,
                        n_train,n_validation,n_test,metrics)
                       VALUES (%s,%s,'listwise_mlp_v1','shadow',%s,%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (version, role, list(feature_names), psycopg2.extras.Json(artifact), artifact_hash,
                     dataset_hash, train_days[0], train_days[-1], validation_days[0], validation_days[-1],
                     test_days[0], test_days[-1], int(train.sum()), int(validation.sum()), int(test.sum()),
                     psycopg2.extras.Json(metrics)),
                )
    return card


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.environ.get("VANGUARD_DATABASE_URL", DEFAULT_DSN))
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--cost-pct", type=float, default=0.01)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    connection = psycopg2.connect(args.dsn)
    try:
        examples = load_examples(connection, args.cost_pct)
        train_role(connection, "direction", examples["direction"], DIRECTION_FEATURES, args.epochs, args.write)
        train_role(connection, "contract", examples["contract"], CONTRACT_FEATURES, args.epochs, args.write)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
