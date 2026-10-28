import pytest


@pytest.fixture(autouse=True)
def default_lane_terms(monkeypatch):
    """The live service sets PAPER_OWNER_OVERRIDES; tests start from published defaults unless they opt in."""
    monkeypatch.delenv("PAPER_OWNER_OVERRIDES", raising=False)
