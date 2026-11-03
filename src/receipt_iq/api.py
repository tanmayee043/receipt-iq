"""The HTTP API that the screens call. Run it with:
    uvicorn receipt_iq.api:create_app --factory

    upload ──► size, login and rate checks ──► is it really an image? ──► Postgres: processing
                                                                                   │
                         screen polls GET /documents/{digest} ◄── worker ◄── Redis ┘

Anything that spends a quota needs the login, and every endpoint except the health checks
is rate limited per IP. Behind a proxy, run uvicorn with --proxy-headers and
--forwarded-allow-ips set to the proxy, so limits see the visitor's address and not the
proxy's.

Writes are safe to repeat: the same image uploaded twice is one document, and a second
correction or dismissal is refused rather than applied twice.
"""

import base64
import binascii
import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import datetime
from io import BytesIO
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from kombu.exceptions import OperationalError as BrokerError
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from receipt_iq.db import FAILED, Document, session_scope
from receipt_iq.review import (
    CorrectionRejected,
    Cursor,
    DocumentNotFound,
    NotReviewable,
    begin_processing,
    correct,
    dismiss,
    get_document,
    review_queue,
    routing_log,
    save_result,
)
from receipt_iq.schema import Receipt
from receipt_iq.security import (
    create_session,
    delete_session,
    seconds_until_allowed,
    session_is_valid,
    verify_password,
)
from receipt_iq.settings import ApiSettings
from receipt_iq.tasks import AUTO, LEADERBOARD, submit, upload_path

logger = logging.getLogger(__name__)

SESSION_COOKIE = "receipt_iq_session"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_BODY_BYTES = MAX_UPLOAD_BYTES + 64 * 1024  # room for the multipart wrapping
MAX_IMAGE_PIXELS = 100_000_000
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png"}
MAX_PAGE_SIZE = 100

Digest = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
PageSize = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def _error_response(
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


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    digest: str
    status: str
    provider: str | None
    receipt: Receipt | None
    corrected_receipt: Receipt | None
    confirmed_mismatch: bool
    issues: list[IssueOut]
    attempts: list[AttemptOut]
    met_target: bool | None
    seconds: float | None
    failure_kind: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
    corrected_at: datetime | None


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    # Pass as `cursor` to get the next page; null on the last one.
    next_cursor: str | None


class LoginIn(BaseModel):
    # The cap stops a huge password being used to keep the hashing busy.
    password: str = Field(max_length=1024)


class SessionOut(BaseModel):
    logged_in: bool


class CorrectionIn(BaseModel):
    receipt: Receipt
    # True to save a correction that doesn't add up, for a receipt that's misprinted.
    confirm_mismatch: bool = False


class DismissalIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


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


class LeaderboardOut(BaseModel):
    split: str
    receipts: int
    providers: list[ProviderOut]


class HealthOut(BaseModel):
    status: str
    checks: dict[str, bool] = {}


def _settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


def _redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def _rate_limited(bucket: str) -> Any:
    def check(request: Request) -> None:
        limit: int = getattr(_settings(request), f"rate_limit_{bucket}")
        client = request.client.host if request.client else "unknown"
        wait = seconds_until_allowed(_redis(request), bucket, client, limit)
        if wait is not None:
            raise ApiError(429, "rate_limited", f"Too many requests. Try again in {wait} s.", wait)

    return Depends(check)


def _require_login(request: Request) -> None:
    if not session_is_valid(_redis(request), request.cookies.get(SESSION_COOKIE)):
        raise ApiError(401, "not_logged_in", "Log in first.")


public = APIRouter(dependencies=[_rate_limited("general")])
protected = APIRouter(dependencies=[_rate_limited("general"), Depends(_require_login)])
health = APIRouter(tags=["health"])


def _encode_cursor(document: Document) -> str:
    raw = json.dumps([document.created_at.isoformat(), document.digest])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str | None) -> Cursor | None:
    if cursor is None:
        return None
    try:
        created_at, digest = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(created_at), str(digest)
    except (ValueError, TypeError, binascii.Error) as failure:
        raise ApiError(400, "invalid_cursor", "That cursor isn't valid.") from failure


def _page(documents: list[Document], limit: int) -> DocumentPage:
    """`documents` holds one row more than the page, which shows whether there's another."""
    shown = documents[:limit]
    more = len(documents) > limit
    return DocumentPage(
        items=[DocumentOut.model_validate(document) for document in shown],
        next_cursor=_encode_cursor(shown[-1]) if more else None,
    )


def _check_image(image: bytes) -> None:
    """Refuse anything but a real JPEG or PNG, whatever the file claims to be."""
    if len(image) > MAX_UPLOAD_BYTES:
        _too_large()
    try:
        with Image.open(BytesIO(image)) as opened:
            width, height = opened.size
            kind = opened.format
            opened.verify()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as failure:
        raise ApiError(415, "not_an_image", "Upload a JPEG or PNG image.") from failure
    if kind not in IMAGE_TYPES:
        raise ApiError(415, "not_an_image", "Upload a JPEG or PNG image.")
    if width * height > MAX_IMAGE_PIXELS:
        raise ApiError(413, "image_too_large", "That image has too many pixels.")


def _too_large() -> NoReturn:
    raise ApiError(413, "too_large", f"Uploads are limited to {MAX_UPLOAD_BYTES // 1024**2} MB.")


def _accept_upload(image: bytes, local_only: bool) -> tuple[Document, bool]:
    digest = hashlib.sha256(image).hexdigest()
    with session_scope() as session:
        queue_it = begin_processing(session, digest)
    if queue_it:
        try:
            submit(image, AUTO, local_only)
        except BrokerError as failure:
            with session_scope() as session:
                save_result(
                    session,
                    {
                        "status": FAILED,
                        "digest": digest,
                        "provider": AUTO,
                        "kind": "queue_unavailable",
                        "reason": "the queue was unreachable, so this was never started",
                    },
                )
            raise ApiError(
                503, "queue_unavailable", "Couldn't queue that. Try again shortly."
            ) from failure
    with session_scope() as session:
        document = get_document(session, digest)
    assert document is not None
    return document, queue_it


UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "local_only": {"type": "boolean", "default": False},
                    },
                }
            }
        },
    }
}


# Reads the form by hand, because FastAPI reads a declared File before it runs
# dependencies. This way, nobody's upload is touched until the login and limits have passed.
@protected.post(
    "/documents",
    response_model=DocumentOut,
    status_code=202,
    dependencies=[_rate_limited("upload")],
    openapi_extra=UPLOAD_SCHEMA,
    responses={200: {"model": DocumentOut, "description": "Already known; nothing was queued"}},
)
async def upload_document(request: Request, response: Response) -> Document:
    async with request.form(max_files=1, max_fields=2) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ApiError(422, "missing_file", "Send the image in a form field named file.")
        image = await upload.read()
        local_only = form.get("local_only") in ("true", "1")
    await run_in_threadpool(_check_image, image)
    document, queued = await run_in_threadpool(_accept_upload, image, local_only)
    response.status_code = 202 if queued else 200
    return document


@protected.get("/documents/{digest}", response_model=DocumentOut)
def read_document(digest: Digest) -> Document:
    with session_scope() as session:
        document = get_document(session, digest)
    if document is None:
        raise DocumentNotFound(digest)
    return document


@protected.get(
    "/documents/{digest}/image",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}},
)
def read_image(digest: Digest) -> Response:
    path = upload_path(digest)
    if not path.exists():
        raise DocumentNotFound(digest)
    image = path.read_bytes()
    media_type = "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"
    return Response(
        image,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@protected.get("/review-queue", response_model=DocumentPage)
def read_review_queue(cursor: str | None = None, limit: PageSize = 50) -> DocumentPage:
    after = _decode_cursor(cursor)
    with session_scope() as session:
        return _page(review_queue(session, limit + 1, after), limit)


@protected.get("/routing-log", response_model=DocumentPage)
def read_routing_log(cursor: str | None = None, limit: PageSize = 50) -> DocumentPage:
    before = _decode_cursor(cursor)
    with session_scope() as session:
        return _page(routing_log(session, limit + 1, before), limit)


@protected.post("/documents/{digest}/correction", response_model=DocumentOut)
def submit_correction(digest: Digest, body: CorrectionIn) -> Document:
    with session_scope() as session:
        return correct(session, digest, body.receipt, body.confirm_mismatch)


@protected.post("/documents/{digest}/dismissal", response_model=DocumentOut)
def submit_dismissal(digest: Digest, body: DismissalIn) -> Document:
    with session_scope() as session:
        return dismiss(session, digest, body.reason)


@protected.post("/logout", response_model=SessionOut)
def logout(request: Request, response: Response) -> SessionOut:
    delete_session(_redis(request), request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return SessionOut(logged_in=False)


@public.post("/login", response_model=SessionOut, dependencies=[_rate_limited("login")])
def login(body: LoginIn, request: Request, response: Response) -> SessionOut:
    settings = _settings(request)
    if not verify_password(settings.owner_password_hash, body.password):
        raise ApiError(401, "wrong_password", "That password isn't right.")
    token = create_session(_redis(request), settings.session_hours)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,  # scripts on the page can't read it
        secure=settings.cookie_secure,
        samesite="strict",  # other sites can't send requests as the logged-in owner
        path="/",
    )
    return SessionOut(logged_in=True)


@public.get("/session", response_model=SessionOut)
def read_session(request: Request) -> SessionOut:
    token = request.cookies.get(SESSION_COOKIE)
    return SessionOut(logged_in=session_is_valid(_redis(request), token))


@public.get("/leaderboard", response_model=LeaderboardOut)
def read_leaderboard() -> Any:
    return json.loads(LEADERBOARD.read_text(encoding="utf-8"))


# Not rate limited: monitoring has to get through, and these must answer when Redis is down.
@health.get("/healthz", response_model=HealthOut)
def healthz() -> HealthOut:
    """The process is running."""
    return HealthOut(status="ok")


@health.get("/readyz", response_model=HealthOut, responses={503: {"model": HealthOut}})
def readyz(request: Request, response: Response) -> HealthOut:
    """Postgres and Redis can be reached, so requests can be served."""
    checks = {"postgres": _postgres_is_up(), "redis": _redis_is_up(_redis(request))}
    ready = all(checks.values())
    response.status_code = 200 if ready else 503
    return HealthOut(status="ok" if ready else "unavailable", checks=checks)


def _postgres_is_up() -> bool:
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
    except OperationalError:
        return False
    return True


def _redis_is_up(redis: Redis) -> bool:
    try:
        return bool(redis.ping())
    except RedisError:
        return False


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
            response = _error_response(413, "too_large", "That request is too large.")
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


def _add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    def api_error(request: Request, error: ApiError) -> JSONResponse:
        headers = {"Retry-After": str(error.details)} if error.status == 429 else None
        details = None if error.status == 429 else error.details
        return _error_response(error.status, error.code, error.message, details, headers)

    @app.exception_handler(DocumentNotFound)
    def not_found(request: Request, error: DocumentNotFound) -> JSONResponse:
        return _error_response(404, "not_found", "There's no receipt with that hash.")

    @app.exception_handler(NotReviewable)
    def not_reviewable(request: Request, error: NotReviewable) -> JSONResponse:
        return _error_response(409, "not_reviewable", str(error))

    @app.exception_handler(CorrectionRejected)
    def rejected(request: Request, error: CorrectionRejected) -> JSONResponse:
        message = "That correction doesn't add up. Confirm it if the receipt itself is misprinted."
        return _error_response(422, "correction_rejected", message, {"issues": error.issues})

    @app.exception_handler(RequestValidationError)
    def invalid(request: Request, error: RequestValidationError) -> JSONResponse:
        # Without the submitted values, so a password is never echoed back.
        details = [
            {"location": item["loc"], "message": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return _error_response(422, "validation_error", "That request isn't valid.", details)

    @app.exception_handler(HTTPException)
    def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return _error_response(
            error.status_code, f"http_{error.status_code}", str(error.detail), None, error.headers
        )

    @app.exception_handler(OperationalError)
    @app.exception_handler(RedisError)
    def service_down(request: Request, error: Exception) -> JSONResponse:
        logger.error("a service is unreachable: %s", type(error).__name__)
        return _error_response(503, "unavailable", "Something's temporarily down. Try again soon.")

    @app.exception_handler(Exception)
    def unexpected(request: Request, error: Exception) -> JSONResponse:
        logger.error("unhandled error", exc_info=error)
        return _error_response(500, "internal_error", "Something went wrong on our side.")


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Build the app. A missing or invalid setting stops it here, not on the first request."""
    settings = settings or ApiSettings()
    app = FastAPI(title="Receipt IQ", version="0.1.0")
    app.state.settings = settings
    app.state.redis = Redis.from_url(
        f"{settings.redis_url}/{settings.api_redis_db}",
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    app.add_middleware(BodyLimit, max_bytes=MAX_BODY_BYTES)
    _add_error_handlers(app)
    app.include_router(protected)
    app.include_router(public)
    app.include_router(health)
    return app
