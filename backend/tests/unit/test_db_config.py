"""P0-T05：資料庫連線設定由 POSTGRES_* 組成，主機可由 NCU_DB_HOST 覆寫。"""

from __future__ import annotations

import pytest

from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clean_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_PORT",
        "NCU_DB_USER",
        "NCU_DB_PASSWORD",
        "NCU_DB_NAME",
        "NCU_DB_HOST",
        "NCU_DB_PORT",
    ):
        monkeypatch.delenv(name, raising=False)


def make(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_url_built_from_postgres_vars_with_localhost_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = make(monkeypatch, POSTGRES_USER="ncu", POSTGRES_PASSWORD="pw", POSTGRES_DB="ncu_db")
    url = s.database_url
    assert url.drivername == "postgresql+asyncpg"
    assert (url.username, url.host, url.port, url.database) == ("ncu", "localhost", 5432, "ncu_db")
    assert url.password == "pw"


def test_host_and_port_overridable_for_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(
        monkeypatch,
        POSTGRES_USER="ncu",
        POSTGRES_PASSWORD="pw",
        POSTGRES_DB="ncu_db",
        POSTGRES_PORT="15432",
        NCU_DB_HOST="db",
        NCU_DB_PORT="5432",
    )
    assert (s.database_url.host, s.database_url.port) == ("db", 5432)


def test_host_port_falls_back_to_postgres_port(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(
        monkeypatch,
        POSTGRES_USER="u",
        POSTGRES_PASSWORD="p",
        POSTGRES_DB="d",
        POSTGRES_PORT="15432",
    )
    assert s.database_url.port == 15432


def test_special_characters_in_password_are_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, POSTGRES_USER="u", POSTGRES_PASSWORD="p@ss:w/rd%", POSTGRES_DB="d")
    rendered = s.database_url.render_as_string(hide_password=False)
    assert "p@ss:w/rd%" not in rendered
    assert s.database_url.password == "p@ss:w/rd%"


def test_password_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, POSTGRES_USER="u", POSTGRES_PASSWORD="topsecret", POSTGRES_DB="d")
    assert "topsecret" not in repr(s)
    assert "topsecret" not in str(s.database_url)


def test_missing_credentials_raise_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, POSTGRES_USER="u", POSTGRES_DB="d")
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        _ = s.database_url
