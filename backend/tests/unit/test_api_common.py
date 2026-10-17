"""P1-T08：API 共用元件（錯誤格式、錯誤碼、cursor 編碼）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError, ErrorCode
from app.api.pagination import decode_cursor, encode_cursor
from app.main import create_app


def test_error_codes_cover_spec_10_1() -> None:
    assert {c.value for c in ErrorCode} >= {
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "NOT_FOUND",
        "RATE_LIMITED",
        "CONFLICT",
        "MODEL_UNAVAILABLE",
    }


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.VALIDATION_ERROR, 422),
        (ErrorCode.UNAUTHORIZED, 401),
        (ErrorCode.FORBIDDEN, 403),
        (ErrorCode.NOT_FOUND, 404),
        (ErrorCode.CONFLICT, 409),
        (ErrorCode.RATE_LIMITED, 429),
        (ErrorCode.MODEL_UNAVAILABLE, 503),
    ],
)
def test_error_codes_map_to_http_status(code: ErrorCode, status: int) -> None:
    assert ApiError(code, "x").status_code == status


def test_unknown_route_returns_not_found_envelope() -> None:
    res = TestClient(create_app()).get("/api/v1/no-such-route")
    assert res.status_code == 404
    body = res.json()
    assert set(body) == {"data", "meta", "error"}
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"
    assert res.headers["X-Request-ID"] == body["meta"]["request_id"]


def test_cursor_round_trip() -> None:
    when = datetime(2026, 10, 17, 4, 0, 1, 123456, tzinfo=UTC)
    run_id = uuid.uuid4()
    assert decode_cursor(encode_cursor(when, run_id)) == (when, run_id)


def test_cursor_keeps_utc_for_other_offsets() -> None:
    when = datetime(2026, 10, 17, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    decoded, _ = decode_cursor(encode_cursor(when, uuid.uuid4()))
    assert decoded == when
    assert decoded.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-base64!!",
        "eyJ0IjogMX0",  # {"t": 1}
        "eyJ0IjogIjIwMjYtMTAtMTdUMDA6MDA6MDAiLCAiaSI6ICJ4In0",  # 無時區、id 非 UUID
    ],
)
def test_invalid_cursor_raises_validation_error(value: str) -> None:
    with pytest.raises(ApiError) as exc:
        decode_cursor(value)
    assert exc.value.code is ErrorCode.VALIDATION_ERROR
