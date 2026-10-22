"""P3-T05：Skills 儀表板 API（`GET /api/v1/admin/skills`）。

每個產品 Skill 回傳目前版本（SKILL.md）、近 7 天執行統計（ai_analysis_runs）與該版本的評測摘要
（eval/reports/<name>/<version>.json，沒有真實模型報告時退回 .recorded.json）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_principal
from app.api.v1.admin.skills import SkillPaths, get_skill_paths
from app.core.security import Principal, Role
from app.main import create_app
from app.models import AIAnalysisRun

pytestmark = pytest.mark.anyio

SKILLS = "/api/v1/admin/skills"
REPO_SKILLS = Path(__file__).resolve().parents[3] / "skills"

ADMIN = Principal(kind="admin", subject="u-1", roles=frozenset({Role.ADMIN}))
INGESTION = Principal(
    kind="service", subject="ingestion", roles=frozenset({Role.SERVICE_INGESTION})
)


def report(*, mode: str = "live", passed: bool = True) -> dict[str, Any]:
    return {
        "skill": "event-classifier",
        "version": "1.0.0",
        "mode": mode,
        "provider": "openai" if mode == "live" else "fake",
        "model": "gpt-5-mini" if mode == "live" else "recorded",
        "split": "test",
        "generated_at": "2026-10-21T03:59:14+00:00",
        "summary": {
            "cases": 28,
            "passed": 27,
            "pass_rate": 0.9643,
            "schema_pass_rate": 1.0,
            "abstain_rate": 0.0357,
            "cost_usd": 0.029082,
        },
        "metrics": [
            {"name": "macro_f1", "value": 0.91, "threshold": 0.85, "passed": True},
        ],
        "gates": {"schema_pass_rate": {"value": 1.0, "threshold": 0.99, "passed": True}},
        "passed": passed,
        "cases": [{"id": "normal-002", "expected": {"category": "power_outage"}}],
    }


@pytest.fixture()
def paths(tmp_path: Path) -> SkillPaths:
    skills = tmp_path / "skills"
    skills.mkdir()
    shutil.copytree(REPO_SKILLS / "event-classifier", skills / "event-classifier")
    shutil.copytree(REPO_SKILLS / "risk-assessor", skills / "risk-assessor")
    (skills / "_template").mkdir()  # 底線開頭的資料夾不是 Skill
    reports = tmp_path / "reports"
    reports.mkdir()
    return SkillPaths(skills_dir=skills, reports_dir=reports)


@pytest.fixture()
async def client(session: AsyncSession, paths: SkillPaths) -> AsyncIterator[httpx2.AsyncClient]:
    app: FastAPI = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = test_session
    app.dependency_overrides[get_principal] = lambda: ADMIN
    app.dependency_overrides[get_skill_paths] = lambda: paths
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://test") as c:
        c.app = app  # type: ignore[attr-defined]
        yield c


def write_report(paths: SkillPaths, name: str, data: dict[str, Any] | str, suffix: str) -> None:
    folder = paths.reports_dir / name
    folder.mkdir(exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    (folder / f"1.0.0{suffix}").write_text(text, encoding="utf-8")


def add_run(
    session: AsyncSession,
    *,
    status: str,
    cost: str,
    version: str = "1.0.0",
    created_at: datetime | None = None,
) -> None:
    session.add(
        AIAnalysisRun(
            skill_name="event-classifier",
            skill_version=version,
            schema_version="1",
            provider="fake",
            model_id="fake",
            status=status,
            attempts=1,
            cost_usd=Decimal(cost),
            trace_id="t",
            created_at=created_at or datetime.now(UTC),
        )
    )


def by_name(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for s in body["data"]}


async def test_lists_skills_with_runs_and_live_eval(
    client: httpx2.AsyncClient, session: AsyncSession, paths: SkillPaths
) -> None:
    add_run(session, status="succeeded", cost="0.010000")
    add_run(session, status="failed", cost="0.002000")
    add_run(session, status="abstained", cost="0.001000", version="0.9.0")
    add_run(
        session, status="succeeded", cost="9", created_at=datetime.now(UTC) - timedelta(days=30)
    )
    await session.commit()
    write_report(paths, "event-classifier", report(), ".json")
    write_report(paths, "event-classifier", report(mode="recorded"), ".recorded.json")

    res = await client.get(SKILLS)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"data", "meta", "error"}
    assert body["meta"]["window_days"] == 7
    skills = by_name(body)
    assert list(skills) == ["event-classifier", "risk-assessor"]

    s = skills["event-classifier"]
    assert s["version"] == "1.0.0"
    assert s["schema_version"] == "1"
    assert s["runs_last_7d"] == {
        "runs": 3,
        "failed": 1,
        "abstained": 1,
        "failure_rate": 0.3333,
        "cost_usd": 0.013,
        "versions": ["0.9.0", "1.0.0"],
    }
    # 真實模型報告優先；只回摘要，不回逐案例內容
    assert s["eval"] == {
        "mode": "live",
        "model": "gpt-5-mini",
        "split": "test",
        "generated_at": "2026-10-21T03:59:14+00:00",
        "passed": True,
        "cases": 28,
        "cases_passed": 27,
        "pass_rate": 0.9643,
        "schema_pass_rate": 1.0,
        "abstain_rate": 0.0357,
        "cost_usd": 0.029082,
        "metrics": [{"name": "macro_f1", "value": 0.91, "threshold": 0.85, "passed": True}],
    }

    idle = skills["risk-assessor"]
    assert idle["runs_last_7d"] == {
        "runs": 0,
        "failed": 0,
        "abstained": 0,
        "failure_rate": 0.0,
        "cost_usd": 0.0,
        "versions": [],
    }
    assert idle["eval"] is None


async def test_falls_back_to_recorded_report(client: httpx2.AsyncClient, paths: SkillPaths) -> None:
    write_report(paths, "event-classifier", report(mode="recorded", passed=False), ".recorded.json")
    s = by_name((await client.get(SKILLS)).json())["event-classifier"]
    assert s["eval"]["mode"] == "recorded"
    assert s["eval"]["passed"] is False


async def test_malformed_report_is_ignored(client: httpx2.AsyncClient, paths: SkillPaths) -> None:
    write_report(paths, "event-classifier", "{not json", ".json")
    write_report(paths, "risk-assessor", json.dumps(["not", "an", "object"]), ".json")
    skills = by_name((await client.get(SKILLS)).json())
    assert skills["event-classifier"]["eval"] is None
    assert skills["risk-assessor"]["eval"] is None


async def test_missing_directories_return_empty_list(
    client: httpx2.AsyncClient, tmp_path: Path
) -> None:
    missing = SkillPaths(skills_dir=tmp_path / "nope", reports_dir=tmp_path / "nope2")
    client.app.dependency_overrides[get_skill_paths] = lambda: missing  # type: ignore[attr-defined]
    res = await client.get(SKILLS)
    assert res.status_code == 200
    assert res.json()["data"] == []


async def test_requires_permission(client: httpx2.AsyncClient) -> None:
    client.app.dependency_overrides[get_principal] = lambda: INGESTION  # type: ignore[attr-defined]
    assert (await client.get(SKILLS)).status_code == 403
