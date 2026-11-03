"""API tests: real Postgres and Redis, with Celery in-process and fake providers.

Celery running in-process means an upload has finished by the time the POST returns.
"""

from collections.abc import Callable, Iterator
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError as BrokerError
from PIL import Image
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from receipt_iq import api, tasks
from receipt_iq.api import MAX_BODY_BYTES, MAX_UPLOAD_BYTES, create_app
from receipt_iq.db import PROCESSING, session_scope
from receipt_iq.evaluation import TemporaryError
from receipt_iq.review import begin_processing, get_document, save_result
from receipt_iq.router import Route
from receipt_iq.schema import LineItem, Receipt
from receipt_iq.security import hash_password
from receipt_iq.settings import ApiSettings, get_settings

PASSWORD = "correct horse battery staple"
PASSWORD_HASH = hash_password(PASSWORD)
TEST_REDIS_DB = 15
GOOD = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(5000))
BAD_MATH = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(9000))
FIXED_BODY = {"receipt": GOOD.model_dump(mode="json")}
DIGEST = "a" * 64

PROTECTED = [
    ("post", "/documents"),
    ("get", f"/documents/{DIGEST}"),
    ("get", f"/documents/{DIGEST}/image"),
    ("get", "/review-queue"),
    ("get", "/routing-log"),
    ("post", f"/documents/{DIGEST}/correction"),
    ("post", f"/documents/{DIGEST}/dismissal"),
    ("post", "/logout"),
]


class FakeProvider:
    name = "fake"
    version = "v1"

    def __init__(self, *outcomes: Receipt | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def extract(self, image: bytes) -> Receipt:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def __call__(self) -> "FakeProvider":
        return self


def image_bytes(color: str = "white", kind: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), color).save(output, kind)
    return output.getvalue()


def make_settings(**overrides: Any) -> ApiSettings:
    values: dict[str, Any] = {
        "owner_password_hash": PASSWORD_HASH,
        "api_redis_db": TEST_REDIS_DB,
        "rate_limit_general": 1000,
        "rate_limit_upload": 1000,
        "rate_limit_login": 1000,
        **overrides,
    }
    return ApiSettings(**values)


@pytest.fixture(autouse=True)
def eager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: None) -> Iterator[None]:
    monkeypatch.setenv("RECEIPT_IQ_DATA", str(tmp_path))
    Redis.from_url(f"{get_settings().redis_url}/{TEST_REDIS_DB}").flushdb()
    tasks.app.conf.update(task_always_eager=True, task_eager_propagates=False)
    tasks.get_extractor.cache_clear()
    tasks.get_router.cache_clear()
    yield
    tasks.app.conf.update(task_always_eager=False)
    tasks.get_extractor.cache_clear()
    tasks.get_router.cache_clear()


# https, because the session cookie is Secure and wouldn't be sent back otherwise.
def make_client(**overrides: Any) -> TestClient:
    app = create_app(make_settings(**overrides))
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


@pytest.fixture
def anonymous() -> TestClient:
    return make_client()


@pytest.fixture
def client(anonymous: TestClient) -> TestClient:
    assert anonymous.post("/login", json={"password": PASSWORD}).status_code == 200
    return anonymous


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeProvider]:
    """Give the router one fake provider, returning these outcomes in turn."""

    def install(*outcomes: Receipt | Exception) -> FakeProvider:
        provider = FakeProvider(*outcomes)
        monkeypatch.setitem(tasks.PROVIDERS, "fake", provider)
        routes = [Route("fake", 0.9, 0.0, 1.0)]
        monkeypatch.setattr(tasks, "load_routes", lambda path: routes)
        tasks.get_router.cache_clear()
        tasks.get_extractor.cache_clear()
        return provider

    return install


def upload(client: TestClient, image: bytes | None = None, **form: str) -> Any:
    files = {"file": ("receipt.png", image if image is not None else image_bytes(), "image/png")}
    return client.post("/documents", files=files, data=form)


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_protected_endpoints_need_the_login(anonymous: TestClient, method: str, path: str) -> None:
    response = anonymous.request(method, path)
    assert response.status_code == 401
    assert error_code(response) == "not_logged_in"


def test_upload_without_login_never_reaches_a_provider(
    anonymous: TestClient, providers: Callable[..., FakeProvider]
) -> None:
    provider = providers(GOOD)
    assert upload(anonymous).status_code == 401
    assert provider.calls == 0


def test_wrong_password_is_refused(anonymous: TestClient) -> None:
    response = anonymous.post("/login", json={"password": "nope"})
    assert (response.status_code, error_code(response)) == (401, "wrong_password")
    assert anonymous.get("/session").json() == {"logged_in": False}


def test_login_cookie_cant_be_read_by_scripts_or_sent_from_other_sites(
    anonymous: TestClient,
) -> None:
    cookie = anonymous.post("/login", json={"password": PASSWORD}).headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie


def test_logout_ends_the_session_for_real(client: TestClient) -> None:
    token = client.cookies["receipt_iq_session"]
    assert client.get("/session").json() == {"logged_in": True}
    assert client.post("/logout").status_code == 200

    # The old cookie stops working too, which a signed cookie couldn't do.
    other = make_client()
    other.cookies.set("receipt_iq_session", token)
    assert other.get("/review-queue").status_code == 401


def test_password_is_never_echoed_in_a_validation_error(anonymous: TestClient) -> None:
    secret = "x" * 2000
    response = anonymous.post("/login", json={"password": secret})
    assert error_code(response) == "validation_error"
    assert secret not in response.text


def test_settings_stop_the_app_at_startup_when_the_hash_is_wrong() -> None:
    with pytest.raises(ValidationError):
        make_settings(owner_password_hash="not-a-hash")


def test_upload_returns_the_answer_with_the_hash(
    client: TestClient, providers: Callable[..., FakeProvider]
) -> None:
    providers(GOOD)
    response = upload(client)
    body = response.json()
    assert response.status_code == 202
    assert body["status"] == "passed"
    assert body["provider"] == "fake"
    assert Receipt.model_validate(body["receipt"]) == GOOD
    assert body["attempts"][0]["outcome"] == "passed"
    assert client.get(f"/documents/{body['digest']}").json()["status"] == "passed"


def test_money_stays_exact_in_json(
    client: TestClient, providers: Callable[..., FakeProvider]
) -> None:
    providers(GOOD)
    assert upload(client).json()["receipt"]["total"] == "5000"


def test_jpeg_is_accepted(client: TestClient, providers: Callable[..., FakeProvider]) -> None:
    providers(GOOD)
    assert upload(client, image_bytes(kind="JPEG")).status_code == 202


def test_same_image_twice_is_one_document_and_one_provider_call(
    client: TestClient, providers: Callable[..., FakeProvider]
) -> None:
    provider = providers(GOOD)
    first, second = upload(client), upload(client)
    assert (first.status_code, second.status_code) == (202, 200)
    assert first.json()["digest"] == second.json()["digest"]
    assert provider.calls == 1


@pytest.mark.parametrize(
    "not_an_image",
    [b"", b"just some text", b"%PDF-1.7 not an image", image_bytes(kind="GIF")],
)
def test_anything_but_a_real_jpeg_or_png_is_refused(
    client: TestClient, providers: Callable[..., FakeProvider], not_an_image: bytes
) -> None:
    provider = providers(GOOD)
    response = upload(client, not_an_image)
    assert (response.status_code, error_code(response)) == (415, "not_an_image")
    assert provider.calls == 0


def test_truncated_image_is_refused(client: TestClient) -> None:
    response = upload(client, image_bytes()[:20])
    assert response.status_code == 415


def test_upload_without_a_file_is_refused(client: TestClient) -> None:
    response = client.post("/documents", data={"local_only": "true"})
    assert (response.status_code, error_code(response)) == (422, "missing_file")


def test_file_just_over_the_limit_is_refused(client: TestClient) -> None:
    response = upload(client, b"x" * (MAX_UPLOAD_BYTES + 1))
    assert (response.status_code, error_code(response)) == (413, "too_large")


def test_request_over_the_limit_is_refused_by_its_declared_size(client: TestClient) -> None:
    response = client.post(
        "/documents", content=b"x", headers={"content-length": str(MAX_BODY_BYTES + 1)}
    )
    assert response.status_code == 413


def test_request_over_the_limit_with_no_declared_size_is_refused(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b"x" * (MAX_BODY_BYTES + 1)

    response = client.post(
        "/documents",
        content=chunks(),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "http_413"


def test_image_with_too_many_pixels_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "MAX_IMAGE_PIXELS", 10)
    response = upload(client)
    assert (response.status_code, error_code(response)) == (413, "image_too_large")


def test_local_only_reaches_the_router(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, providers: Callable[..., FakeProvider]
) -> None:
    cloud = providers(GOOD)
    local = FakeProvider(GOOD)
    monkeypatch.setitem(tasks.PROVIDERS, "on-this-machine", local)
    routes = [Route("fake", 0.9, 0.0, 1.0), Route("on-this-machine", 0.3, 0.0, 1.0, local=True)]
    monkeypatch.setattr(tasks, "load_routes", lambda path: routes)
    tasks.get_router.cache_clear()

    assert upload(client, local_only="true").json()["provider"] == "on-this-machine"
    assert cloud.calls == 0


def test_setup_problem_shows_as_failed_and_can_be_uploaded_again(
    client: TestClient, providers: Callable[..., FakeProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = FakeProvider(ValueError("a bug in our code"))
    monkeypatch.setitem(tasks.PROVIDERS, "fake", broken)
    monkeypatch.setattr(tasks, "load_routes", lambda path: [Route("fake", 0.9, 0.0, 1.0)])
    tasks.get_router.cache_clear()
    first = upload(client)
    body = first.json()
    assert body["status"] == "failed"
    assert body["failure_kind"] == "task_failed"
    assert "a bug in our code" in body["failure_reason"]

    assert client.get("/review-queue").json()["items"] == []

    tasks.get_extractor.cache_clear()
    providers(GOOD)
    second = upload(client)
    assert (second.status_code, second.json()["status"]) == (202, "passed")
    assert second.json()["failure_reason"] is None


def test_failed_task_does_not_erase_an_answer(client: TestClient) -> None:
    with session_scope() as session:
        save_result(
            session,
            {
                "digest": DIGEST,
                "status": "passed",
                "provider": "gpt",
                "receipt": GOOD.model_dump(mode="json"),
            },
        )
    tasks.ExtractionTask().on_failure(ValueError("late"), "id", [DIGEST, "gpt"], {}, None)
    assert client.get(f"/documents/{DIGEST}").json()["status"] == "passed"


def test_queue_down_is_reported_and_the_upload_can_be_retried(
    client: TestClient, providers: Callable[..., FakeProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    providers(GOOD)
    real_submit = tasks.submit

    def broker_down(*args: Any) -> None:
        raise BrokerError("connection refused")

    monkeypatch.setattr(api, "submit", broker_down)
    response = upload(client)
    assert (response.status_code, error_code(response)) == (503, "queue_unavailable")

    monkeypatch.setattr(api, "submit", real_submit)
    assert upload(client).json()["status"] == "passed"


def test_all_providers_busy_stays_processing(
    client: TestClient, providers: Callable[..., FakeProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    # In a real worker the retry happens later, so show what a screen sees meanwhile.
    providers(GOOD)
    monkeypatch.setattr(api, "submit", lambda *args: None)
    response = upload(client)
    assert (response.status_code, response.json()["status"]) == (202, PROCESSING)
    assert upload(client).status_code == 200


def test_retries_that_run_out_end_up_in_the_review_queue(
    client: TestClient, providers: Callable[..., FakeProvider]
) -> None:
    providers(*[TemporaryError("down") for _ in range(tasks.MAX_RETRIES + 1)])
    body = upload(client).json()
    assert body["status"] == "dead_letter"
    assert [item["digest"] for item in client.get("/review-queue").json()["items"]] == [
        body["digest"]
    ]


def test_processing_receipt_cant_be_corrected(client: TestClient) -> None:
    with session_scope() as session:
        begin_processing(session, DIGEST)
    response = client.post(f"/documents/{DIGEST}/correction", json=FIXED_BODY)
    assert (response.status_code, error_code(response)) == (409, "not_reviewable")


def test_image_comes_back_with_its_type(
    client: TestClient, providers: Callable[..., FakeProvider]
) -> None:
    providers(GOOD, GOOD)
    png, jpeg = image_bytes(), image_bytes(kind="JPEG")
    for image, media_type in ((png, "image/png"), (jpeg, "image/jpeg")):
        digest = upload(client, image).json()["digest"]
        response = client.get(f"/documents/{digest}/image")
        assert response.content == image
        assert response.headers["content-type"] == media_type
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("bad", ["..%2F..%2Fsecret", "ABC", "a" * 63, "g" * 64])
def test_hash_in_the_path_must_be_a_real_hash(client: TestClient, bad: str) -> None:
    assert client.get(f"/documents/{bad}/image").status_code in (404, 422)
    assert client.get(f"/documents/{bad}").status_code in (404, 422)


def test_missing_document_is_a_404_in_the_usual_format(client: TestClient) -> None:
    for path in (f"/documents/{DIGEST}", f"/documents/{DIGEST}/image"):
        response = client.get(path)
        assert (response.status_code, error_code(response)) == (404, "not_found")


def test_unknown_path_uses_the_usual_error_format(anonymous: TestClient) -> None:
    assert error_code(anonymous.get("/nope")) == "http_404"


def queued(digest_letter: str) -> str:
    digest = digest_letter * 64
    with session_scope() as session:
        save_result(
            session,
            {
                "digest": digest,
                "status": "needs_review",
                "provider": "auto",
                "receipt": BAD_MATH.model_dump(mode="json"),
                "attempts": [
                    {"provider": "gpt", "outcome": "needs_review", "reason": "", "seconds": 1.5}
                ],
            },
        )
    return digest


def test_queue_pages_through_everything_once_with_a_cursor(client: TestClient) -> None:
    digests = [queued(letter) for letter in "cab"]
    seen = []
    cursor = None
    for _ in range(4):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/review-queue", params=params).json()
        seen += [item["digest"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == digests


def test_routing_log_is_newest_first_and_pages(client: TestClient) -> None:
    digests = [queued(letter) for letter in "abc"]
    first = client.get("/routing-log", params={"limit": 2}).json()
    assert [item["digest"] for item in first["items"]] == [digests[2], digests[1]]
    assert first["items"][0]["attempts"][0]["provider"] == "gpt"
    second = client.get("/routing-log", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [item["digest"] for item in second["items"]] == [digests[0]]
    assert second["next_cursor"] is None


def test_bad_cursor_and_bad_limit_are_refused(client: TestClient) -> None:
    assert error_code(client.get("/review-queue", params={"cursor": "%%%"})) == "invalid_cursor"
    assert client.get("/review-queue", params={"limit": 101}).status_code == 422


def test_correction_works_and_a_second_is_refused(client: TestClient) -> None:
    digest = queued("a")
    response = client.post(f"/documents/{digest}/correction", json=FIXED_BODY)
    assert response.status_code == 200
    assert response.json()["status"] == "corrected"
    assert Receipt.model_validate(response.json()["receipt"]) == BAD_MATH
    assert client.get("/review-queue").json()["items"] == []

    again = client.post(f"/documents/{digest}/correction", json=FIXED_BODY)
    assert (again.status_code, error_code(again)) == (409, "not_reviewable")


def test_correction_that_doesnt_add_up_needs_confirming(client: TestClient) -> None:
    digest = queued("a")
    body = {"receipt": BAD_MATH.model_dump(mode="json")}
    refused = client.post(f"/documents/{digest}/correction", json=body)
    assert (refused.status_code, error_code(refused)) == (422, "correction_rejected")
    assert "total says 9000" in refused.json()["error"]["details"]["issues"][0]

    confirmed = client.post(
        f"/documents/{digest}/correction", json={**body, "confirm_mismatch": True}
    )
    assert confirmed.json()["confirmed_mismatch"] is True


def test_correction_with_a_negative_amount_is_refused(client: TestClient) -> None:
    digest = queued("a")
    response = client.post(
        f"/documents/{digest}/correction", json={"receipt": {"items": [], "total": "-1"}}
    )
    assert error_code(response) == "validation_error"


def test_dismissal(client: TestClient) -> None:
    digest = queued("a")
    response = client.post(f"/documents/{digest}/dismissal", json={"reason": "not a receipt"})
    assert response.json()["status"] == "dismissed"
    missing = client.post(f"/documents/{'b' * 64}/dismissal", json={"reason": "x"})
    assert missing.status_code == 404
    assert client.post(f"/documents/{digest}/dismissal", json={"reason": ""}).status_code == 422


def test_leaderboard_is_public_and_matches_the_file(anonymous: TestClient) -> None:
    body = anonymous.get("/leaderboard").json()
    assert body["receipts"] > 0
    assert {"local", "gpt"} <= {provider["provider"] for provider in body["providers"]}


def test_general_rate_limit_answers_429_with_a_wait() -> None:
    client = make_client(rate_limit_general=2)
    statuses = [client.get("/leaderboard").status_code for _ in range(3)]
    assert statuses == [200, 200, 429]
    response = client.get("/leaderboard")
    assert error_code(response) == "rate_limited"
    assert 0 < int(response.headers["retry-after"]) <= 61


def test_login_rate_limit_slows_password_guessing() -> None:
    client = make_client(rate_limit_login=2)
    statuses = [client.post("/login", json={"password": "guess"}).status_code for _ in range(3)]
    assert statuses == [401, 401, 429]


def test_upload_rate_limit_stops_uploads_before_a_provider_is_called(
    providers: Callable[..., FakeProvider],
) -> None:
    provider = providers(GOOD, GOOD)
    client = make_client(rate_limit_upload=1)
    client.post("/login", json={"password": PASSWORD})
    assert upload(client, image_bytes("white")).status_code == 202
    assert upload(client, image_bytes("black")).status_code == 429
    assert provider.calls == 1


def test_healthz_and_readyz(anonymous: TestClient) -> None:
    assert anonymous.get("/healthz").json()["status"] == "ok"
    ready = anonymous.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"postgres": True, "redis": True}


def broken_redis(client: TestClient) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("down")

    redis = client.app.state.redis  # type: ignore[attr-defined]
    redis.execute_command = fail
    redis.pipeline = fail


def test_redis_down_refuses_requests_rather_than_dropping_the_limits(
    anonymous: TestClient,
) -> None:
    broken_redis(anonymous)
    response = anonymous.get("/leaderboard")
    assert (response.status_code, error_code(response)) == (503, "unavailable")


def test_health_checks_still_answer_with_redis_down(anonymous: TestClient) -> None:
    broken_redis(anonymous)
    assert anonymous.get("/healthz").status_code == 200
    ready = anonymous.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["checks"]["redis"] is False


def test_postgres_down_is_a_503_and_not_a_stack_trace(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nothing")
    response = client.get("/review-queue")
    assert (response.status_code, error_code(response)) == (503, "unavailable")
    assert client.get("/readyz").json()["checks"]["postgres"] is False


def test_unexpected_error_hides_the_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: Any) -> None:
        raise ValueError("secret internal detail")

    monkeypatch.setattr(api, "review_queue", explode)
    response = client.get("/review-queue")
    assert (response.status_code, error_code(response)) == (500, "internal_error")
    assert "secret internal detail" not in response.text


def test_openapi_describes_the_upload(anonymous: TestClient) -> None:
    schema = anonymous.get("/openapi.json").json()
    body = schema["paths"]["/documents"]["post"]["requestBody"]
    assert "multipart/form-data" in body["content"]


def test_saved_row_matches_what_the_api_returns(
    client: TestClient, providers: Callable[..., FakeProvider]
) -> None:
    providers(BAD_MATH)
    body = upload(client).json()
    assert body["status"] == "needs_review"
    assert body["issues"][0]["location"] == "total"
    with session_scope() as session:
        document = get_document(session, body["digest"])
    assert document is not None
    assert document.issues[0]["expected"] == body["issues"][0]["expected"]
