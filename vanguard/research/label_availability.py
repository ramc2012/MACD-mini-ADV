"""Chronological research splits with label-level, rather than fixed-day, purging.

A label cannot train a model whose validation decision precedes the end of
that label or its recorded availability. Equality is also purged: simultaneous
observations do not establish which event was processed first. Missing timing
provenance fails closed. No model registration or trading writes occur here.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import numpy as np


def _timestamp(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("label/decision timestamps must be timezone aware")
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, timezone.utc)
    raise TypeError(f"expected date/datetime timing provenance, got {type(value).__name__}")


def purged_chronological_split(sessions, label_ends, available_ats=None,
                               decision_times=None, *, as_of=None,
                               min_train_sessions=10, min_partition_sessions=8):
    """Return train/validation/test masks and retained source-session lists.

    Boundaries are fixed from source sessions first (60/20/20 approximately).
    Earlier partition examples are then removed individually if their effective
    label availability reaches the next partition's first decision. Delayed
    ingestion counts, even for short holding horizons. Test labels must have
    completed by ``as_of`` when supplied. This never moves boundaries to rescue
    a failing dataset. Reconstructed histories must identify themselves outside
    this helper and must never substitute completion for actual availability
    while claiming prospective evidence.
    """
    count = len(sessions)
    if len(label_ends) != count:
        raise ValueError("one label end is required per source example")
    available_ats = label_ends if available_ats is None else available_ats
    decision_times = sessions if decision_times is None else decision_times
    if len(available_ats) != count or len(decision_times) != count:
        raise ValueError("timing provenance length differs from source examples")
    unique = sorted(set(sessions))
    if len(unique) < 30:
        raise RuntimeError(f"only {len(unique)} source sessions; need at least 30")
    test_n = max(min_partition_sessions, round(len(unique) * 0.20))
    validation_n = max(min_partition_sessions, round(len(unique) * 0.20))
    validation_start = len(unique) - test_n - validation_n
    test_start = len(unique) - test_n
    if validation_start < min_train_sessions:
        raise RuntimeError("not enough source sessions for chronological partitions")
    train_set = set(unique[:validation_start])
    validation_set = set(unique[validation_start:test_start])
    test_set = set(unique[test_start:])
    decisions = [_timestamp(value) for value in decision_times]
    if any(value is None for value in decisions):
        raise ValueError("decision timing provenance is required")
    validation_boundary = min(ts for day, ts in zip(sessions, decisions) if day in validation_set)
    test_boundary = min(ts for day, ts in zip(sessions, decisions) if day in test_set)
    horizon = _timestamp(as_of)
    ends = [_timestamp(value) for value in label_ends]
    availability = [_timestamp(value) for value in available_ats]
    known = [max(end, available) if end is not None and available is not None else None
             for end, available in zip(ends, availability)]

    def mask(days, boundary=None):
        return np.asarray([
            day in days and known_at is not None
            and (boundary is None or known_at < boundary)
            and (horizon is None or known_at <= horizon)
            for day, known_at in zip(sessions, known)
        ], dtype=bool)

    train, validation, test = mask(train_set, validation_boundary), mask(validation_set, test_boundary), mask(test_set)
    retained = [sorted({day for day, keep in zip(sessions, part) if keep})
                for part in (train, validation, test)]
    return train, validation, test, *retained


def require_usable_partitions(split, *, min_train_sessions=10, min_validation_sessions=5):
    """Stop training when purging exposes insufficient evidence."""
    if len(split[3]) < min_train_sessions or len(split[4]) < min_validation_sessions or not split[2].any():
        raise RuntimeError(
            "insufficient label-available chronology after purging: "
            f"train={len(split[3])}, validation={len(split[4])}, test={len(split[5])} sessions"
        )
    return split
