"""The public part of the site: the screens, the leaderboard, the demo and the error format.

None of it needs a login, a database, Redis or a provider key, so it can also run on its own as
the hosted demo:

    uvicorn receipt_iq.web:create_demo_app --factory

The full API (api.py) serves all of this too, with the owner's pages added.
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.responses import Response as StarletteResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from receipt_iq.review import TOLERANCE, DocumentNotFound
from receipt_iq.router import (
    DEFAULT_MAX_SECONDS,
    DEFAULT_TARGET,
    LEADERBOARD,
    load_routes,
    qualifies,
    rank,
)
from receipt_iq.schema import Receipt
from receipt_iq.security import LocalRateLimit
from receipt_iq.settings import SiteSettings
from receipt_iq.validation import validate

logger = logging.getLogger(__name__)

# A demo edit is a small JSON receipt; nothing bigger has a reason to arrive.
DEMO_MAX_BODY_BYTES = 64 * 1024


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def error_response(
    status: int,
    code: str,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status, headers=headers)


def too_many_requests(wait: int | None) -> None:
    if wait is not None:
        raise ApiError(429, "rate_limited", f"Too many requests. Try again in {wait} s.", wait)


class IssueOut(BaseModel):
    code: str
    message: str
    expected: str
    actual: str
    location: str


class AttemptOut(BaseModel):
    provider: str
    outcome: str
    reason: str
    seconds: float


class SessionOut(BaseModel):
    logged_in: bool
    # True on the hosted demo, which has no owner pages to log in to.
    demo_only: bool = False


class ProviderOut(BaseModel):
    provider: str
    local: bool
    fully_correct: float
    total_correct: float
    item_f1: float
    catch_rate: float | None
    false_alarm_rate: float | None
    failure_rate: float
    median_seconds: float
    cost_per_1000: float
    cost_note: str
    # Reaches the accuracy target and speed limit, so the router doesn't keep it as a last resort.
    meets_policy: bool


class LeaderboardOut(BaseModel):
    split: str
    receipts: int
    accuracy_target: float
    max_seconds: float
    # The order the router tries providers in.
    routing_order: list[str]
    providers: list[ProviderOut]


class DemoSampleOut(BaseModel):
    id: str
    title: str
    note: str
    status: str
    provider: str
    receipt: Receipt
    issues: list[IssueOut]
    attempts: list[AttemptOut]
    seconds: float


class DemoCheckIn(BaseModel):
    receipt: Receipt


class DemoCheckOut(BaseModel):
    passed: bool
    issues: list[str]


class HealthOut(BaseModel):
    status: str
    checks: dict[str, bool] = {}


# Rate limited by whichever app includes them, as the full API and the demo count differently.
showcase = APIRouter(prefix="/api")
live = APIRouter(tags=["health"])


@showcase.get("/leaderboard", response_model=LeaderboardOut)
def read_leaderboard() -> Any:
    leaderboard = json.loads(LEADERBOARD.read_text(encoding="utf-8"))
    routes = load_routes(LEADERBOARD)
    meeting = {r.provider for r in routes if qualifies(r, DEFAULT_TARGET, DEFAULT_MAX_SECONDS)}
    for provider in leaderboard["providers"]:
        provider["meets_policy"] = provider["provider"] in meeting
    order = rank(routes, DEFAULT_TARGET, max_seconds=DEFAULT_MAX_SECONDS)
    return {
        **leaderboard,
        "accuracy_target": DEFAULT_TARGET,
        "max_seconds": DEFAULT_MAX_SECONDS,
        "routing_order": [route.provider for route in order],
    }


def _demo_samples(request: Request) -> list[dict[str, Any]]:
    settings: SiteSettings = request.app.state.settings
    path = settings.demo_dir / "samples.json"
    if not path.exists():
        return []
    samples: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return samples


@showcase.get("/demo/samples", response_model=list[DemoSampleOut])
def read_demo_samples(request: Request) -> Any:
    """Saved answers to sample receipts, so a visitor spends no provider quota."""
    return _demo_samples(request)


@showcase.get(
    "/demo/samples/{sample_id}/image",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def read_demo_image(sample_id: str, request: Request) -> Response:
    # Looked up among the samples, so the address can't reach any other file.
    if not any(sample["id"] == sample_id for sample in _demo_samples(request)):
        raise DocumentNotFound(sample_id)
    settings: SiteSettings = request.app.state.settings
    path = settings.demo_dir / "images" / f"{sample_id}.jpg"
    return Response(
        path.read_bytes(),
        media_type="image/jpeg",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=3600"},
    )


@showcase.post("/demo/check", response_model=DemoCheckOut)
def check_demo_receipt(body: DemoCheckIn) -> DemoCheckOut:
    """The real validator on a visitor's edit. Nothing is saved, and no provider is called."""
    checked = validate(body.receipt, TOLERANCE)
    return DemoCheckOut(passed=checked.passed, issues=[i.message for i in checked.issues])


# Not rate limited: monitoring has to get through.
@live.get("/healthz", response_model=HealthOut)
def healthz() -> HealthOut:
    """The process is running."""
    return HealthOut(status="ok")


class BodyLimit:
    """Refuse an oversized request before it fills the disk, even with no Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > self.max_bytes:
            response = error_response(413, "too_large", "That request is too large.")
            await response(scope, receive, send)
            return

        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > self.max_bytes:
                # An HTTPException, as FastAPI turns anything else raised while reading the
                # body into a 400.
                raise HTTPException(413, "That request is too large.")
            return message

        await self.app(scope, counted, send)


class Screens(StaticFiles):
    """The built screens. Any page address gets index.html, and the app shows the right page.

    A missing file (one with an extension) or API address stays a 404, so a broken script
    link or a mistyped API call doesn't get a web page back.
    """

    HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; "
            # Inline styles because the chart library positions its elements with them.
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        ),
    }

    async def get_response(self, path: str, scope: Scope) -> StarletteResponse:
        # The address as requested: `path` has had any ".." resolved, and uses backslashes
        # on Windows, so it can hide that an address was meant for the API.
        address = scope["path"]
        if address == "/api" or address.startswith("/api/"):
            raise HTTPException(404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as failure:
            is_file = "." in address.rsplit("/", 1)[-1]
            if failure.status_code != 404 or is_file:
                raise
            response = await super().get_response("index.html", scope)
        response.headers.update(self.HEADERS)
        return response


def add_error_handlers(app: FastAPI) -> None:
    """Every error, from either app, comes back as {"error": {"code", "message", ...}}."""

    @app.exception_handler(ApiError)
    def api_error(request: Request, error: ApiError) -> JSONResponse:
        headers = {"Retry-After": str(error.details)} if error.status == 429 else None
        details = None if error.status == 429 else error.details
        return error_response(error.status, error.code, error.message, details, headers)

    @app.exception_handler(DocumentNotFound)
    def not_found(request: Request, error: DocumentNotFound) -> JSONResponse:
        return error_response(404, "not_found", "There's no receipt with that hash.")

    @app.exception_handler(RequestValidationError)
    def invalid(request: Request, error: RequestValidationError) -> JSONResponse:
        # Without the submitted values, so a password is never echoed back.
        details = [
            {"location": item["loc"], "message": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return error_response(422, "validation_error", "That request isn't valid.", details)

    @app.exception_handler(HTTPException)
    def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return error_response(
            error.status_code, f"http_{error.status_code}", str(error.detail), None, error.headers
        )

    @app.exception_handler(Exception)
    def unexpected(request: Request, error: Exception) -> JSONResponse:
        logger.error("unhandled error", exc_info=error)
        return error_response(500, "internal_error", "Something went wrong on our side.")


def create_demo_app(settings: SiteSettings | None = None) -> FastAPI:
    """Only the public pages, for hosting. Nothing here can spend a provider's quota."""
    settings = settings or SiteSettings()
    app = FastAPI(
        title="Receipt IQ demo",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    # One process and no Redis, so requests are counted in memory.
    limit = LocalRateLimit(settings.rate_limit_general)

    def rate_limited(request: Request) -> None:
        too_many_requests(
            limit.seconds_until_allowed(request.client.host if request.client else "unknown")
        )

    @app.get("/api/session", response_model=SessionOut, dependencies=[Depends(rate_limited)])
    def read_session() -> SessionOut:
        return SessionOut(logged_in=False, demo_only=True)

    app.add_middleware(BodyLimit, max_bytes=DEMO_MAX_BODY_BYTES)
    add_error_handlers(app)
    app.include_router(showcase, dependencies=[Depends(rate_limited)])
    app.include_router(live)
    if settings.frontend_dist.is_dir():
        app.mount("/", Screens(directory=settings.frontend_dist, html=True), name="screens")
    return app
