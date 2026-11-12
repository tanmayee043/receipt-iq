"""The hosted demo: the public pages on their own, with no database, Redis or keys."""

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from receipt_iq import security
from receipt_iq.schema import LineItem, Receipt
from receipt_iq.security import LocalRateLimit
from receipt_iq.settings import SiteSettings
from receipt_iq.web import DEMO_MAX_BODY_BYTES, create_demo_app

FROZEN_TIME = 1_800_000_000.0  # the start of a one-minute window
GOOD = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(5000))
BAD_MATH = Receipt(items=[LineItem(name="TEA", total_price=Decimal(5000))], total=Decimal(9000))


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: FROZEN_TIME))


def make_client(tmp_path: Path, **overrides: Any) -> TestClient:
    folder = tmp_path / "demo"
    (folder / "images").mkdir(parents=True, exist_ok=True)
    (folder / "images" / "sample-1.jpg").write_bytes(b"jpeg bytes")
    sample = {
        "id": "sample-1",
        "title": "An easy one",
        "note": "It added up.",
        "status": "passed",
        "provider": "gpt",
        "receipt": GOOD.model_dump(mode="json"),
        "issues": [],
        "attempts": [{"provider": "gpt", "outcome": "passed", "reason": "", "seconds": 8.3}],
        "seconds": 8.3,
    }
    (folder / "samples.json").write_text(json.dumps([sample]), encoding="utf-8")
    values: dict[str, Any] = {
        "demo_dir": folder,
        "frontend_dist": Path("does-not-exist"),
        "rate_limit_general": 1000,
        **overrides,
    }
    return TestClient(create_demo_app(SiteSettings(**values)))


def test_starts_with_no_database_redis_or_keys(tmp_path: Path) -> None:
    # A fresh process in an empty folder, so no .env or environment variable can help it.
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_URL", "_KEY", "_HASH"))}
    script = (
        "import sys\n"
        "from receipt_iq.web import create_demo_app\n"
        "create_demo_app()\n"
        "assert 'receipt_iq.tasks' not in sys.modules, 'the demo loaded the task queue'\n"
    )
    subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, check=True)


def test_serves_the_public_pages(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/api/leaderboard").json()["receipts"] > 0
    assert client.get("/api/demo/samples").json()[0]["id"] == "sample-1"
    assert client.get("/api/demo/samples/sample-1/image").content == b"jpeg bytes"
    assert client.get("/api/demo/samples/secret/image").status_code == 404


def test_check_runs_the_real_validator(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    good = client.post("/api/demo/check", json={"receipt": GOOD.model_dump(mode="json")})
    assert good.json() == {"passed": True, "issues": []}
    bad = client.post("/api/demo/check", json={"receipt": BAD_MATH.model_dump(mode="json")})
    assert bad.json()["passed"] is False


def test_has_no_owner_pages(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    assert client.get("/api/session").json() == {"logged_in": False, "demo_only": True}
    assert client.post("/api/login", json={"password": "anything"}).status_code == 404
    assert client.get("/api/review-queue").status_code == 404
    assert client.post("/api/documents").status_code == 404
    assert client.get("/readyz").status_code == 404


def test_is_rate_limited_per_minute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(tmp_path, rate_limit_general=2)
    assert client.get("/api/leaderboard").status_code == 200
    assert client.get("/api/demo/samples").status_code == 200
    refused = client.get("/api/leaderboard")
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert refused.headers["Retry-After"] == "61"
    # Monitoring still gets through.
    assert client.get("/healthz").status_code == 200

    later = FROZEN_TIME + 60
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: later))
    assert client.get("/api/leaderboard").status_code == 200


def test_refuses_large_requests(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    body = b"x" * (DEMO_MAX_BODY_BYTES + 1)
    response = client.post("/api/demo/check", content=body)
    assert response.status_code == 413


def test_local_rate_limit_counts_each_visitor_apart() -> None:
    limit = LocalRateLimit(1)
    assert limit.seconds_until_allowed("1.1.1.1") is None
    assert limit.seconds_until_allowed("2.2.2.2") is None
    assert limit.seconds_until_allowed("1.1.1.1") == 61
