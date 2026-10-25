"""Read-only public projections of Vanguard's local decision/research artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNTIME = Path("/legacy/vanguard")
MAX_BYTES = 2_000_000


def read_status(path: Path, *, now: datetime | None = None,
                max_age_seconds: int | None = None) -> dict[str, Any]:
    """Never run a worker, repair a file, or turn an absent result into success."""
    result: dict[str, Any] = {"status": "unavailable", "paper_only": True,
                              "promotion_allowed": False, "admissions_allowed": False,
                              "selection_allowed": False, "publication_allowed": False,
                              "held_exit_management_allowed": True}
    try:
        if path.stat().st_size > MAX_BYTES:
            return {**result, "status": "invalid", "detail": "Status artifact exceeds the supported size."}
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("Expected a status object")
    except FileNotFoundError:
        return {**result, "detail": "The worker has not published this status yet."}
    except (OSError, ValueError, TypeError):
        return {**result, "status": "invalid", "detail": "Status artifact cannot be read."}

    # These files are public summaries. Do not expose registration paths,
    # model coefficients, training rows, environment data or arbitrary extras.
    public = {
        "status", "detail", "observed_at", "generated_at", "generated_at_utc", "as_of",
        "session", "session_date", "phase", "stages", "models", "recovery", "blockers",
        "admissions_allowed", "selection_allowed", "publication_allowed",
        "held_exit_management_allowed", "freshness", "policy", "missed_publication",
        "prospective_start", "training_window", "validation", "horizons", "baselines",
        "cost_scenarios", "limitations", "mode", "classification", "report_version",
        "features", "targets", "sample_counts", "execution_basis", "research_only",
        "missed_sessions", "expected_bar", "universe", "min_breadth_fraction",
        "costscenarios", "dataset_sha256",
    }
    result.update({key: value for key, value in data.items() if key in public})
    result.update(paper_only=True, promotion_allowed=False)
    if max_age_seconds is not None:
        raw = data.get("observed_at") or data.get("generated_at_utc") or data.get("generated_at")
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Timestamp has no timezone")
            age = ((now or datetime.now(timezone.utc))-stamp).total_seconds()
            stale = age > max_age_seconds or age < -30
            result["artifact_age_seconds"] = round(age, 1)
        except (TypeError, ValueError):
            stale = True
            result["artifact_age_seconds"] = None
        result["artifact_stale"] = stale
        if stale:
            result.update(status="stale", admissions_allowed=False,
                          selection_allowed=False, publication_allowed=False,
                          held_exit_management_allowed=True)
            result["detail"] = "Decision-readiness heartbeat is stale; new admissions await current evidence."
    return result


def decision_status(*, now: datetime | None = None) -> dict[str, Any]:
    return read_status(RUNTIME / "decision_readiness.json", now=now, max_age_seconds=180)


def challenger_status() -> dict[str, Any]:
    return read_status(RUNTIME / "equity_challenger/summary.json")
