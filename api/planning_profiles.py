"""Read-only planning modes. These rules never submit orders or alter a portfolio."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config import MAX_POSITION_VALUE, MIN_STOCK_PRICE
from paper_trading.trading_plan import (
    CONSENSUS_MODE,
    DEFAULT_TARGET_POSITION_VALUE,
    plan_mode_options,
)


class CompetitionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str = CONSENSUS_MODE
    universe_mode: Literal["EXPANDED", "BROAD"] = "EXPANDED"
    target_position_value: float = Field(
        default=DEFAULT_TARGET_POSITION_VALUE, gt=0, le=MAX_POSITION_VALUE,
        allow_inf_nan=False,
    )
    minimum_price_mode: Literal["ANY", "CUSTOM", "NO_MINIMUM"] = "ANY"
    minimum_price: float = Field(default=MIN_STOCK_PRICE, ge=0, le=1_000_000, allow_inf_nan=False)
    stop_loss_enabled: bool = False
    take_profit_enabled: bool = False
    app_settings: bool = False

    @model_validator(mode="after")
    def valid_custom_floor(self):
        if self.minimum_price_mode == "CUSTOM" and self.minimum_price <= 0:
            raise ValueError("Custom minimum price must be greater than zero.")
        return self

    @field_validator("strategy")
    @classmethod
    def valid_strategy(cls, value: str) -> str:
        if value not in plan_mode_options():
            raise ValueError("Select a registered strategy or planning mode.")
        return value


class TradingViewRules(BaseModel):
    """User-chosen illustrative limits, not TradingView account settings."""

    model_config = ConfigDict(extra="forbid")

    max_position_value: float = Field(default=20_000, gt=0, le=1_000_000, allow_inf_nan=False)
    risk_per_trade_pct: float = Field(default=1, gt=0, le=10, allow_inf_nan=False)
    stop_loss_pct: float = Field(default=5, gt=0, lt=100, allow_inf_nan=False)
    take_profit_pct: float = Field(default=10, gt=0, le=500, allow_inf_nan=False)


class PlanningProfileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    mode: Literal["COMPETITION", "TRADINGVIEW"]
    rules: dict[str, Any]

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Profile name is required.")
        return cleaned

    @model_validator(mode="after")
    def validate_mode_rules(self):
        rule_type = CompetitionRules if self.mode == "COMPETITION" else TradingViewRules
        self.rules = rule_type.model_validate(self.rules).model_dump()
        return self


class PlanningProfileUpdate(PlanningProfileWrite):
    expected_updated_at: datetime


class TradingViewEstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_price: float = Field(gt=0, allow_inf_nan=False)
    available_cash: float = Field(ge=0, allow_inf_nan=False)
    account_equity: float = Field(gt=0, allow_inf_nan=False)
    rules: TradingViewRules


class TradingViewAutoPlanRequest(BaseModel):
    """Only paper-account sizing inputs; stops and limits are server chosen."""

    ticker: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.\-]+$")
    available_cash: float = Field(ge=0, allow_inf_nan=False)
    account_equity: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()


def estimate_long_position(request: TradingViewEstimateRequest) -> dict:
    """Whole-share, long-only hypothetical size. No market or account I/O."""
    rules = request.rules
    risk_budget = request.account_equity * rules.risk_per_trade_pct / 100
    risk_per_share = request.entry_price * rules.stop_loss_pct / 100
    shares = min(
        math.floor(request.available_cash / request.entry_price),
        math.floor(rules.max_position_value / request.entry_price),
        math.floor(risk_budget / risk_per_share),
    )
    position_value = shares * request.entry_price
    return {
        "shares": shares,
        "position_value": round(position_value, 2),
        "cash_remaining": round(request.available_cash - position_value, 2),
        "stop_watch_price": round(request.entry_price * (1 - rules.stop_loss_pct / 100), 4),
        "take_profit_watch_price": round(request.entry_price * (1 + rules.take_profit_pct / 100), 4),
        "planned_risk_at_stop": round(shares * risk_per_share, 2),
        "risk_budget": round(risk_budget, 2),
        "warning": (
            "Hypothetical long-only calculation. These are watch levels, not "
            "TradingView orders. A price gap or delayed/manual response can lose "
            "more than the planned risk. No account is connected or modified."
        ),
    }
