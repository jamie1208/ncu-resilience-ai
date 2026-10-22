"""Skills 儀表板（P3-T05）：`GET /api/v1/admin/skills`，需要 `events:read_all`。

每個產品 Skill（`skills/`，只讀 metadata）回傳：
- 目前版本（SKILL.md 的 metadata.version、schema_version）
- 近 7 天執行統計（ai_analysis_runs：執行數、失敗與 abstain 數、失敗率、費用、出現過的版本）
- 目前版本的評測摘要：`eval/reports/<name>/<version>.json`（真實模型）優先，沒有時退回
  `<version>.recorded.json`；檔案不存在或格式不正確時為 null。只回摘要，不回逐案例內容。
目錄不存在時回空清單（例如容器未掛載 skills/、eval/）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.eval.runner import DEFAULT_REPORTS
from app.ai.runner.loader import SKILLS_DIR, discover_skills
from app.api.deps import get_db_session, require_permission
from app.api.responses import envelope
from app.core.security import Permission, Principal
from app.models.ai import AIAnalysisRun

router = APIRouter(prefix="/skills", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_db_session)]
CanRead = Annotated[Principal, Depends(require_permission(Permission.EVENTS_READ_ALL))]

WINDOW = timedelta(days=7)
EVAL_SUMMARY_FIELDS = ("pass_rate", "schema_pass_rate", "abstain_rate", "cost_usd")


@dataclass(frozen=True)
class SkillPaths:
    """產品 Skills 與評測報告的位置（測試時以 dependency override 替換）。"""

    skills_dir: Path
    reports_dir: Path


def get_skill_paths() -> SkillPaths:
    """預設位置：repo 的 `skills/` 與 `eval/reports/`。"""
    return SkillPaths(skills_dir=SKILLS_DIR, reports_dir=DEFAULT_REPORTS)


Paths = Annotated[SkillPaths, Depends(get_skill_paths)]


def _empty_runs() -> dict[str, Any]:
    return {
        "runs": 0,
        "failed": 0,
        "abstained": 0,
        "failure_rate": 0.0,
        "cost_usd": 0.0,
        "versions": [],
    }


async def _runs(session: AsyncSession, since: datetime) -> dict[str, dict[str, Any]]:
    run = AIAnalysisRun
    rows = await session.execute(
        select(
            run.skill_name,
            run.skill_version,
            func.count(),
            func.count().filter(run.status == "failed"),
            func.count().filter(run.status == "abstained"),
            func.coalesce(func.sum(run.cost_usd), 0),
        )
        .where(run.created_at >= since)
        .group_by(run.skill_name, run.skill_version)
    )
    stats: dict[str, dict[str, Any]] = {}
    for name, version, runs, failed, abstained, cost in rows:
        s = stats.setdefault(name, _empty_runs())
        s["runs"] += runs
        s["failed"] += failed
        s["abstained"] += abstained
        s["cost_usd"] += float(cost)
        s["versions"].append(version)
    for s in stats.values():
        s["failure_rate"] = round(s["failed"] / s["runs"], 4) if s["runs"] else 0.0
        s["cost_usd"] = round(s["cost_usd"], 6)
        s["versions"].sort()
    return stats


def _read_report(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _eval_summary(reports_dir: Path, name: str, version: str) -> dict[str, Any] | None:
    folder = reports_dir / name
    for filename in (f"{version}.json", f"{version}.recorded.json"):
        report = _read_report(folder / filename)
        if report is None:
            continue
        summary = report.get("summary")
        metrics = report.get("metrics")
        if not isinstance(summary, dict) or not isinstance(metrics, list):
            continue
        return {
            "mode": report.get("mode"),
            "model": report.get("model"),
            "split": report.get("split"),
            "generated_at": report.get("generated_at"),
            "passed": report.get("passed") is True,
            "cases": summary.get("cases"),
            "cases_passed": summary.get("passed"),
            **{key: summary.get(key) for key in EVAL_SUMMARY_FIELDS},
            "metrics": [
                {k: m.get(k) for k in ("name", "value", "threshold", "passed")}
                for m in metrics
                if isinstance(m, dict)
            ],
        }
    return None


@router.get("")
async def list_skills(session: Session, _: CanRead, paths: Paths) -> dict[str, Any]:
    """各產品 Skill 的版本、近 7 天執行統計與評測摘要（依名稱排序）。"""
    if not paths.skills_dir.is_dir():
        return envelope([], window_days=WINDOW.days)
    skills = discover_skills(paths.skills_dir)
    runs = await _runs(session, datetime.now(UTC) - WINDOW)
    data = [
        {
            "name": skill.name,
            "version": skill.version,
            "schema_version": skill.schema_version,
            "runs_last_7d": runs.get(skill.name, _empty_runs()),
            "eval": _eval_summary(paths.reports_dir, skill.name, skill.version),
        }
        for skill in skills.values()
    ]
    return envelope(data, window_days=WINDOW.days)
