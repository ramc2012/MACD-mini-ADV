"""Request models and helpers shared by the strategy and desk APIs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OrderInput(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    lots: int = Field(default=1, ge=1)
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: float | None = Field(default=None, gt=0)


def _as_json(order) -> dict:
    from dataclasses import asdict
    from datetime import datetime as _dt
    row = asdict(order)
    return {k: (v.isoformat() if isinstance(v, _dt) else v) for k, v in row.items()}
