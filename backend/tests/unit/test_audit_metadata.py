"""P0-T12：稽核 metadata 的檢查與正規化（不需要資料庫）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.domain.states import EventStatus
from app.services.audit import MAX_STRING_LENGTH, AuditMetadataError, prepare_metadata

# --- 欄位名稱：以分段比對，避免誤擋 -------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "prompt_tokens",
        "completion_tokens",
        "content_hash",
        "session_count",
        "context",
        "idempotency_key",
        "text_length",
        "fields",
    ],
)
def test_harmless_keys_are_allowed(key: str) -> None:
    assert prepare_metadata({key: 1}) == {key: 1}


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "password_hash",
        "client_secret",
        "access_token",
        "token",
        "api_key",
        "apiKey",
        "API-KEY",
        "authorization",
        "cookie",
        "email",
        "user_email",
        "phone",
        "national_id",
        "credentials",
        "report_text",
        "raw_content",
        "body",
        "requestBody",
    ],
)
def test_sensitive_keys_are_rejected(key: str) -> None:
    with pytest.raises(AuditMetadataError):
        prepare_metadata({key: "x"})


def test_sensitive_keys_rejected_when_nested() -> None:
    with pytest.raises(AuditMetadataError):
        prepare_metadata({"outer": [{"inner": {"access_token": "x"}}]})


# --- 值：個資樣式與長度 -------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "聯絡 someone@example.com",
        "0912-345-678",
        "0912345678",
        "A123456789",
        "x" * (MAX_STRING_LENGTH + 1),
    ],
)
def test_sensitive_values_are_rejected(value: str) -> None:
    with pytest.raises(AuditMetadataError):
        prepare_metadata({"note": value})


def test_sensitive_values_rejected_in_lists() -> None:
    with pytest.raises(AuditMetadataError):
        prepare_metadata({"fields": ["code", "0912345678"]})


def test_ordinary_values_are_allowed() -> None:
    meta = {"from": "unverified", "to": "verified", "fields": ["code", "name"], "count": 3}
    assert prepare_metadata(meta) == meta


# --- 型別正規化：存進 JSONB 前轉成 JSON 可接受的值 -------------------------------------


def test_uuid_datetime_and_enum_are_normalized() -> None:
    eid = uuid.uuid4()
    at = datetime(2026, 10, 17, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    out = prepare_metadata(
        {"event_id": eid, "at": at, "status": EventStatus.VERIFIED, "ids": (eid,)}
    )
    assert out == {
        "event_id": str(eid),
        "at": "2026-10-17T12:00:00+00:00",
        "status": "verified",
        "ids": [str(eid)],
    }


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(AuditMetadataError):
        prepare_metadata({"at": datetime(2026, 10, 17, 12, 0)})


@pytest.mark.parametrize("value", [object(), {1, 2}, b"bytes"])
def test_unsupported_types_are_rejected(value: Any) -> None:
    with pytest.raises(AuditMetadataError):
        prepare_metadata({"value": value})


def test_non_string_keys_are_rejected() -> None:
    bad: dict[Any, Any] = {1: "x"}
    with pytest.raises(AuditMetadataError):
        prepare_metadata(bad)


def test_size_limit() -> None:
    with pytest.raises(AuditMetadataError):
        prepare_metadata({f"k{i}": "x" * 100 for i in range(60)})


def test_aware_utc_datetime_round_trip() -> None:
    at = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    assert prepare_metadata({"at": at}) == {"at": "2026-10-17T12:00:00+00:00"}
