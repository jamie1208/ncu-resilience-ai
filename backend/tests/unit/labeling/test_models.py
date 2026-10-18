"""P1-T09：標記紀錄模型與一致性規則（ADR-0009）。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.states import VerificationStatus
from app.labeling.models import Category, ClaimType, LabelRecord, schema_text

SCHEMA_FILE = Path(__file__).resolve().parents[4] / "data" / "labeling" / "label.schema.json"
T0 = datetime(2026, 10, 18, 4, 0, tzinfo=UTC)


def record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "item_id": str(uuid.UUID(int=1)),
        "source_code": "ncu-announcements",
        "content_hash": "a" * 64,
        "published_at": "2026-10-17T08:00:00+00:00",
        "is_campus_relevant": True,
        "category": "facility",
        "claim_type": "official",
        "verification": "verified",
        "event_group": "evt-ee5-power-1018",
        "related_groups": [],
        "high_risk": False,
        "mentions_person": False,
        "tags": ["停電"],
        "labeler": "human",
        "labeled_at": T0.isoformat(),
    }
    base.update(overrides)
    return base


def test_valid_event_record() -> None:
    label = LabelRecord.model_validate(record())
    assert label.category is Category.FACILITY
    assert label.claim_type is ClaimType.OFFICIAL
    assert label.verification is VerificationStatus.VERIFIED


def test_valid_non_event_record() -> None:
    label = LabelRecord.model_validate(
        record(category="none", verification=None, event_group=None, tags=[])
    )
    assert label.category is Category.NONE


def test_taxonomy_is_the_nine_decided_categories() -> None:
    assert {c.value for c in Category} == {
        "facility",
        "weather",
        "security",
        "traffic",
        "health",
        "fire",
        "academic",
        "infosec",
        "none",
    }
    assert {c.value for c in ClaimType} == {"official", "first_hand", "hearsay", "rumor"}


@pytest.mark.parametrize(
    "overrides",
    [
        # 與校園無關：只能是非事件
        {"is_campus_relevant": False},
        {
            "is_campus_relevant": False,
            "category": "none",
            "verification": None,
            "event_group": None,
            "high_risk": True,
        },
        # 非事件不能有查證結果、事件分組或高風險
        {"category": "none", "event_group": None},
        {"category": "none", "verification": None},
        {
            "category": "none",
            "verification": None,
            "event_group": None,
            "related_groups": ["evt-a"],
        },
        {"category": "none", "verification": None, "event_group": None, "high_risk": True},
        # 事件必須有查證結果與事件分組
        {"verification": None},
        {"event_group": None},
        # 相關事件不能包含自己
        {"related_groups": ["evt-ee5-power-1018"]},
    ],
)
def test_inconsistent_records_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        LabelRecord.model_validate(record(**overrides))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("item_id", "not-a-uuid"),
        ("content_hash", "abc"),
        ("category", "other"),
        ("claim_type", "news"),
        ("verification", "confirmed"),
        ("event_group", "EVT 1"),
        ("event_group", "e" * 65),
        ("related_groups", ["evt-a", "evt-a"]),
        ("tags", ["a" * 21]),
        ("tags", ["有,逗號"]),
        ("tags", [f"t{i}" for i in range(11)]),
        ("labeler", "ai"),
        ("labeled_at", "2026-10-18T12:00:00"),  # 必須有時區
        ("notes", "王小明說的"),  # 不收自由文字，避免個資
    ],
)
def test_invalid_fields_are_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        LabelRecord.model_validate(record(**{field: value}))


def test_times_are_stored_as_utc() -> None:
    label = LabelRecord.model_validate(
        record(published_at="2026-10-17T16:00:00+08:00", labeled_at="2026-10-18T12:00:00+08:00")
    )
    assert label.published_at == datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    assert label.labeled_at == T0


def test_published_at_may_be_missing() -> None:
    assert LabelRecord.model_validate(record(published_at=None)).published_at is None


def test_record_round_trips_through_json() -> None:
    label = LabelRecord.model_validate(record())
    assert LabelRecord.model_validate_json(label.model_dump_json()) == label


def test_schema_file_matches_model() -> None:
    """label.schema.json 由模型產生；模型改了要重新產生（python -m app.labeling.models）。"""
    assert SCHEMA_FILE.read_text(encoding="utf-8") == schema_text()
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
