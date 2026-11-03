"""A real Postgres for tests, built by running the migrations, so those get tested too."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from receipt_iq.db import CONNECT_TIMEOUT_SECONDS, database_url, get_engine, session_scope

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
TEST_DATABASE = "receipt_iq_test"


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    main = make_url(database_url())
    admin = create_engine(
        main,
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )
    try:
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {TEST_DATABASE} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {TEST_DATABASE}"))
    except OperationalError as failure:
        pytest.fail(f"can't reach Postgres; start it with: docker compose up -d ({failure})")
    finally:
        admin.dispose()

    url = main.set(database=TEST_DATABASE).render_as_string(hide_password=False)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        command.upgrade(Config(ALEMBIC_INI), "head")
    yield url


@pytest.fixture
def database(test_database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty database for this test. Tests call session_scope() themselves."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    with get_engine().begin() as connection:
        connection.execute(text("TRUNCATE documents"))


@pytest.fixture
def session(database: None) -> Iterator[Session]:
    with session_scope() as open_session:
        yield open_session
