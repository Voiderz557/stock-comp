"""FastAPI entrypoint for the public-app prototype.

Run locally with:
    uvicorn api.public_app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import os
import asyncio
import math
from datetime import datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from api.auth import Principal, require_principal
from api.cloud_repository import (
    cloud_repository_configured,
    trusted_plan_storage_configured,
    validate_trusted_plan_storage_configuration,
    create_competition_account,
    load_account_snapshot,
    update_competition_account_snapshot,
    save_current_trading_plan,
    load_plan_history,
    load_recent_paper_actions,
    record_recommendation_paper_action,
    change_recommendation_status,
    list_planning_profiles,
    create_planning_profile,
    update_planning_profile,
)
from api.plan_service import build_cloud_plan, plan_payload
from api.plan_jobs import PlanJobs
from api.snapshot_visibility import visible_snapshot
from api.account_export import export_owned_account_data
from api.account_deletion import preview_deletion, confirm_deletion
from api.external_research import get_external_research
from api.journal_service import load_journal
from api.planning_profiles import (
    PlanningProfileWrite,
    PlanningProfileUpdate,
    TradingViewEstimateRequest,
    TradingViewAutoPlanRequest,
    estimate_long_position,
)
from api.tradingview_auto import AutoPlanUnavailable, load_auto_long_plan
from market.vix import load_vix_context
from paper_trading.trading_plan import CONSENSUS_MODE, DEFAULT_TARGET_POSITION_VALUE, plan_mode_options
from config import MAX_POSITION_VALUE, MIN_STOCK_PRICE
from api.demo_data import demo_snapshot
from api.security import (
    FixedWindowRateLimiter,
    client_rate_key,
    positive_int_env,
    rate_limited_response,
    secure_response,
)


# Leave time for the account-version-checked save before the browser's 180s
# request timeout. A timed-out build may finish populating price cache, but
# this coroutine must never save a plan after the client has given up.
LIVE_PLAN_BUILD_TIMEOUT_SECONDS = 150
RESEARCH_REQUEST_TIMEOUT_SECONDS = 10


def _allowed_origins() -> list[str]:
    configured = os.getenv("STOCK_COMP_ALLOWED_ORIGINS")
    if configured:
        origins = [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
        if "*" in origins:
            raise RuntimeError("STOCK_COMP_ALLOWED_ORIGINS must list explicit origins; wildcard CORS is forbidden.")
        if any(not origin.startswith(("https://", "http://localhost", "http://127.0.0.1")) for origin in origins):
            raise RuntimeError("STOCK_COMP_ALLOWED_ORIGINS must use HTTPS except for localhost development.")
        return origins
    return ["http://127.0.0.1:3000", "http://localhost:3000", "http://127.0.0.1:4173"]


class AccountDeletionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    challenge: str = Field(min_length=20, max_length=100)
    confirmation: str = Field(max_length=100)
    acknowledged: bool = Field(strict=True)


class AccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    starting_capital: float = Field(ge=0)
    cash: float = Field(ge=0)
    snapshot_as_of: datetime

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Account name cannot be blank.")
        return cleaned


class PositionSnapshotRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.\-]+$")
    side: str
    quantity: float = Field(gt=0)
    average_price: float | None = Field(default=None, ge=0)
    exact_cost_basis: float | None = Field(default=None, ge=0)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("side")
    @classmethod
    def normalize_side(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"LONG", "SHORT"}:
            raise ValueError("Position side must be LONG or SHORT.")
        return normalized

    def model_post_init(self, __context) -> None:
        if self.average_price is None and self.exact_cost_basis is None:
            raise ValueError("An average price or exact cost basis is required.")


class AccountUpdateRequest(AccountCreateRequest):
    positions: list[PositionSnapshotRequest] = Field(default_factory=list, max_length=200)
    expected_portfolio_version: int = Field(gt=0)

    @field_validator("positions")
    @classmethod
    def unique_positions(cls, value: list[PositionSnapshotRequest]) -> list[PositionSnapshotRequest]:
        keys = [(position.ticker, position.side) for position in value]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate ticker and side combinations are not allowed.")
        return value


class PlanCreateRequest(BaseModel):
    strategy: str
    universe_mode: str = Field(default="EXPANDED", pattern="^(EXPANDED|BROAD)$")
    target_position_value: float = Field(default=DEFAULT_TARGET_POSITION_VALUE, gt=0, le=MAX_POSITION_VALUE, allow_inf_nan=False)
    minimum_price: float = Field(default=MIN_STOCK_PRICE, ge=0, le=1_000_000, allow_inf_nan=False)

    @field_validator("strategy")
    @classmethod
    def registered_strategy(cls, value: str) -> str:
        if value not in plan_mode_options():
            raise ValueError("Strategy is not available.")
        return value


class PlanJobRequest(PlanCreateRequest):
    request_id: UUID


class RecommendationEventRequest(BaseModel):
    event_type: str

    @field_validator("event_type")
    @classmethod
    def supported_event(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DISMISSED", "REOPENED"}:
            raise ValueError("Only dismissal and reopening are available.")
        return normalized


class PaperActionRequest(BaseModel):
    recommendation_id: UUID
    quantity: int = Field(gt=0, le=1_000_000)
    expected_portfolio_version: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=200)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Stock Competition Public API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )
    limiter = FixedWindowRateLimiter()
    plan_jobs = PlanJobs()
    application.state.plan_jobs = plan_jobs
    read_limit = positive_int_env("STOCK_COMP_READ_RATE_LIMIT_PER_MINUTE", 120)
    write_limit = positive_int_env("STOCK_COMP_WRITE_RATE_LIMIT_PER_MINUTE", 20)

    @application.middleware("http")
    async def security_controls(request, call_next):
        request_class = "write" if request.method in {"POST", "PUT", "PATCH", "DELETE"} else "read"
        limit = write_limit if request_class == "write" else read_limit
        allowed, remaining, retry_after = limiter.check(client_rate_key(request, request_class), limit)
        if not allowed:
            return rate_limited_response(limit, retry_after)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return secure_response(response)

    @application.get("/v1/health")
    def health() -> dict:
        return {"status": "ok", "version": application.version}

    @application.get("/v1/config")
    def public_config() -> dict:
        return {
            "authentication_configured": bool((os.getenv("STOCK_COMP_SUPABASE_URL") or "").strip()),
            "demo_mode": (os.getenv("STOCK_COMP_API_DEMO_MODE") or "").lower() in {"1", "true", "yes", "on"},
        }

    @application.get("/v1/snapshot")
    async def snapshot(
        account_id: UUID | None = None,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        if principal.is_demo:
            return demo_snapshot()
        if not principal.access_token or not cloud_repository_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authenticated account storage is not configured yet.",
            )
        return visible_snapshot(await load_account_snapshot(principal.access_token, account_id))

    @application.post("/v1/accounts", status_code=status.HTTP_201_CREATED)
    async def create_account(
        request: AccountCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        if principal.is_demo:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo access cannot create accounts.")
        if not principal.access_token or not cloud_repository_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authenticated account storage is not configured yet.",
            )
        return visible_snapshot(await create_competition_account(
            principal.access_token,
            name=request.name,
            starting_capital=request.starting_capital,
            cash=request.cash,
            snapshot_as_of=request.snapshot_as_of.isoformat(),
        ))

    @application.put("/v1/accounts/current")
    async def update_account(
        request: AccountUpdateRequest,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        if principal.is_demo:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo access cannot update accounts.")
        if not principal.access_token or not cloud_repository_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authenticated account storage is not configured yet.",
            )
        return visible_snapshot(await update_competition_account_snapshot(
            principal.access_token,
            name=request.name,
            starting_capital=request.starting_capital,
            cash=request.cash,
            snapshot_as_of=request.snapshot_as_of.isoformat(),
            positions=[position.model_dump() for position in request.positions],
            expected_portfolio_version=request.expected_portfolio_version,
        ))

    @application.get("/v1/strategies")
    def strategies(principal: Principal = Depends(require_principal)) -> dict:
        return {"strategies": plan_mode_options()}

    @application.post("/v1/account-deletion/preview")
    async def deletion_preview(principal: Principal = Depends(require_principal)) -> dict:
        try:
            return await asyncio.wait_for(preview_deletion(principal), timeout=45)
        except asyncio.TimeoutError:
            raise HTTPException(504, "Deletion review timed out. Nothing was deleted.") from None

    @application.post("/v1/account-deletion/confirm")
    async def deletion_confirm(request: AccountDeletionRequest, principal: Principal = Depends(require_principal)) -> dict:
        if plan_jobs.active is not None and plan_jobs.active.owner == principal.user_id:
            raise HTTPException(409, "Wait for your active market scan to finish before deleting the account.")
        return await confirm_deletion(principal, request.challenge, request.confirmation, request.acknowledged)

    @application.get("/v1/account-export")
    async def account_export(principal: Principal = Depends(require_principal)) -> dict:
        if principal.is_demo:
            raise HTTPException(status_code=403, detail="Demo access cannot export an account.")
        if not principal.access_token or not cloud_repository_configured():
            raise HTTPException(status_code=503, detail="Authenticated account storage is not configured yet.")
        return await export_owned_account_data(principal.access_token)

    @application.get("/v1/planning-profiles")
    async def planning_profiles(principal: Principal = Depends(require_principal)) -> list[dict]:
        if principal.is_demo:
            return []
        if not principal.access_token:
            raise HTTPException(status_code=401, detail="Sign in to load planning profiles.")
        return await list_planning_profiles(principal.access_token)

    @application.post("/v1/planning-profiles", status_code=status.HTTP_201_CREATED)
    async def create_profile(
        request: PlanningProfileWrite,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        if principal.is_demo:
            raise HTTPException(status_code=403, detail="Demo access cannot save profiles.")
        if not principal.access_token:
            raise HTTPException(status_code=401, detail="Sign in to save a planning profile.")
        return await create_planning_profile(principal.access_token, request.model_dump())

    @application.put("/v1/planning-profiles/{profile_id}")
    async def edit_profile(
        profile_id: UUID,
        request: PlanningProfileUpdate,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        if principal.is_demo:
            raise HTTPException(status_code=403, detail="Demo access cannot save profiles.")
        if not principal.access_token:
            raise HTTPException(status_code=401, detail="Sign in to update a planning profile.")
        return await update_planning_profile(
            principal.access_token,
            profile_id,
            request.model_dump(exclude={"expected_updated_at"}),
            request.expected_updated_at.isoformat(),
        )

    @application.post("/v1/planning-profiles/estimate")
    def tradingview_estimate(
        request: TradingViewEstimateRequest,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        return estimate_long_position(request)

    @application.post("/v1/planning-profiles/auto-plan", include_in_schema=False)
    @application.post("/v1/plans/exit-guidance")
    async def exit_guidance(
        request: TradingViewAutoPlanRequest,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        if principal.is_demo or not principal.access_token:
            raise HTTPException(status_code=403, detail="Sign in to review a current BUY candidate.")
        current = visible_snapshot(await load_account_snapshot(principal.access_token))
        plan = current.get("plan") or {}
        if plan.get("status") != "CURRENT":
            raise HTTPException(status_code=409, detail="Build a fresh plan first.")
        candidate = next((item for item in current.get("recommendations", [])
                          if item.get("ticker") == request.ticker and item.get("action") == "BUY"
                          and item.get("status") == "OPEN"), None)
        if candidate is None:
            raise HTTPException(status_code=422, detail="This ticker is not a current BUY candidate.")
        saved_price = candidate.get("price")
        if not isinstance(saved_price, (int, float)) or not math.isfinite(saved_price) or saved_price <= 0:
            raise HTTPException(status_code=422, detail="The saved candidate has no usable dated price.")
        try:
            result = await asyncio.wait_for(asyncio.to_thread(
                load_auto_long_plan, request.ticker,
                available_cash=request.available_cash,
                account_equity=request.account_equity,
            ), timeout=45)
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=503, detail="Daily price history timed out; no sizing was produced.") from exc
        except AutoPlanUnavailable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        market_date = str(plan.get("market_as_of") or "")[:10]
        if result["price_as_of"] != market_date:
            raise HTTPException(status_code=409, detail="Candidate signal and daily close have different dates. Rebuild the plan.")
        if abs(result["entry_price"] - saved_price) / saved_price > 0.005:
            raise HTTPException(status_code=409, detail="Candidate and sizing prices disagree. Rebuild the plan.")
        return result

    @application.get("/v1/research/company/{ticker}")
    async def company_research(
        ticker: str,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        """Read-only provider metadata; credentials never reach the provider."""
        if principal.is_demo:
            raise HTTPException(status_code=403, detail="Sign in to load company research.")
        if not principal.access_token:
            raise HTTPException(status_code=401, detail="Sign in to load company research.")
        try:
            return await asyncio.wait_for(
                get_external_research(ticker), timeout=RESEARCH_REQUEST_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=503, detail="Company research timed out.") from exc

    @application.get("/v1/recommendation-journal")
    async def recommendation_journal(
        principal: Principal = Depends(require_principal),
    ) -> dict:
        """Explicit refresh of caller-owned history with bounded cached prices."""
        if principal.is_demo:
            raise HTTPException(status_code=403, detail="Sign in to load your recommendation journal.")
        if not principal.access_token:
            raise HTTPException(status_code=401, detail="Sign in to load your recommendation journal.")
        if not cloud_repository_configured():
            raise HTTPException(status_code=503, detail="Authenticated account storage is not configured yet.")
        return await load_journal(await load_plan_history(principal.access_token))

    @application.get("/v1/market/vix")
    async def vix_context(principal: Principal = Depends(require_principal)) -> dict:
        """Read-only market context; never changes an account or strategy signal."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(load_vix_context), timeout=20)
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=503, detail="VIX market data timed out.") from exc

    @application.post("/v1/plan-jobs", status_code=202)
    async def start_plan_job(request: PlanJobRequest, principal: Principal = Depends(require_principal)):
        if principal.is_demo:
            raise HTTPException(403, "Demo access cannot generate plans.")
        if not principal.access_token or not cloud_repository_configured() or not trusted_plan_storage_configured():
            raise HTTPException(503, "Authenticated trusted plan storage is not configured. No scan was started.")
        validate_trusted_plan_storage_configuration()
        current = await load_account_snapshot(principal.access_token)
        fingerprint = (current["account"]["portfolio_version"], request.strategy,
                       request.target_position_value, request.minimum_price, request.universe_mode)

        async def execute(progress, timeout, job):
            worker = asyncio.create_task(asyncio.to_thread(
                build_cloud_plan, current, request.strategy, request.target_position_value,
                request.minimum_price, request.universe_mode, status_callback=progress,
            ))
            try:
                generated = await asyncio.wait_for(asyncio.shield(worker), timeout)
            except asyncio.TimeoutError as error:
                from time import monotonic
                job.status, job.stage = "FAILED", "Scan time limit reached"
                job.error = "The market scan exceeded its safety limit. No new plan was saved. Wait for price loading to finish before retrying."
                job.finished = monotonic()
                # Python cannot stop a running thread. Retain the single scan
                # slot until it exits; never let its late result reach storage.
                await asyncio.gather(worker, return_exceptions=True)
                raise HTTPException(504, job.error) from error
            progress("Saving checked plan", "Rechecking the portfolio version before saving.")
            payload = plan_payload(current, generated)
            await save_current_trading_plan(principal.access_token, payload, user_id=principal.user_id)

        job = plan_jobs.start(principal.user_id, str(request.request_id), fingerprint, execute)
        return job.public()

    @application.get("/v1/plan-jobs/{job_id}")
    async def plan_job_status(job_id: UUID, principal: Principal = Depends(require_principal)):
        if principal.is_demo or not principal.access_token:
            raise HTTPException(403, "Sign in to read your scan progress.")
        job = plan_jobs.get(principal.user_id, job_id)
        result = job.public()
        if job.status == "SUCCEEDED":
            result["snapshot"] = visible_snapshot(await load_account_snapshot(principal.access_token))
        return result

    @application.post("/v1/plans/current")
    async def build_plan(
        request: PlanCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        if principal.is_demo:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo access cannot generate plans.")
        if not principal.access_token or not cloud_repository_configured():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authenticated account storage is not configured yet.")
        if not trusted_plan_storage_configured():
            raise HTTPException(status_code=503, detail="Trusted plan storage is not configured on the server. No plan was generated or saved.")
        validate_trusted_plan_storage_configuration()
        current = await load_account_snapshot(principal.access_token)
        try:
            generated = await asyncio.wait_for(asyncio.to_thread(
                build_cloud_plan, current, request.strategy, request.target_position_value,
                request.minimum_price, request.universe_mode,
            ), timeout=LIVE_PLAN_BUILD_TIMEOUT_SECONDS)
            payload = plan_payload(current, generated)
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail="The market scan exceeded the safe request limit. No new plan was saved. Price-cache warmup may continue briefly; refresh account state before retrying.",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return visible_snapshot(await save_current_trading_plan(principal.access_token, payload, user_id=principal.user_id))

    @application.get("/v1/plans/history")
    async def plan_history(principal: Principal = Depends(require_principal)) -> list[dict]:
        if principal.is_demo:
            return []
        if not principal.access_token or not cloud_repository_configured():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authenticated account storage is not configured yet.")
        return await load_plan_history(principal.access_token)

    @application.get("/v1/paper-actions")
    async def paper_action_history(principal: Principal = Depends(require_principal)) -> list[dict]:
        if principal.is_demo:
            return []
        if not principal.access_token or not cloud_repository_configured():
            raise HTTPException(status_code=503, detail="Authenticated account storage is not configured yet.")
        return await load_recent_paper_actions(principal.access_token)

    @application.post("/v1/paper-actions")
    async def record_paper_action(
        request: PaperActionRequest,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        if principal.is_demo:
            raise HTTPException(status_code=403, detail="Demo access cannot record paper actions.")
        if not principal.access_token or not cloud_repository_configured():
            raise HTTPException(status_code=503, detail="Authenticated account storage is not configured yet.")
        result = await record_recommendation_paper_action(
            principal.access_token,
            recommendation_id=request.recommendation_id,
            quantity=request.quantity,
            expected_portfolio_version=request.expected_portfolio_version,
            idempotency_key=request.idempotency_key,
        )
        result["snapshot"] = visible_snapshot(result["snapshot"])
        return result

    @application.post("/v1/recommendations/{recommendation_id}/events")
    async def recommendation_event(
        recommendation_id: UUID,
        request: RecommendationEventRequest,
        principal: Principal = Depends(require_principal),
    ) -> dict:
        if principal.is_demo:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo access cannot change recommendations.")
        if not principal.access_token or not cloud_repository_configured():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authenticated account storage is not configured yet.")
        return visible_snapshot(await change_recommendation_status(principal.access_token, recommendation_id, request.event_type))

    return application


app = create_app()
