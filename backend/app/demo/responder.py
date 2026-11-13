"""示範模式的假模型（P9-T01，ADR-0022）：依虛構資料集與 Skill 的輸入組出合法的輸出。

只供種子使用，不呼叫任何 API。Skill Runner 照常檢查 schema、evidence_id、原句與個資，所以
這裡組出的輸出必須和真實模型一樣通過驗證：引用的 id 都取自輸入，原句都從證據原文逐字截取。
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from app.ai.llm.base import LLMResult, Message, Usage
from app.demo.dataset import RISK_SCORES, Article, Dataset, EventSpec, Finding, SeriesSpec
from app.labeling.models import ClaimType
from app.verification.rules import Assessment, Stance, decide

_SKILL_INPUT = re.compile(r"<skill_input>\n(.*?)\n</skill_input>", re.S)
_EVIDENCE = re.compile(
    r'<untrusted_source id="([^"]*)" source="([^"]*)"[^>]*>\n(.*?)\n</untrusted_source>', re.S
)
QUOTE_CHARS = 16


def parse_payload(messages: Sequence[Message]) -> dict[str, Any]:
    """從 Skill Runner 組好的 user 訊息還原輸入：`<skill_input>` 的欄位加上 `evidence`。"""
    user = next(m.content for m in messages if m.role == "user")
    found = _SKILL_INPUT.search(user)
    payload: dict[str, Any] = json.loads(found.group(1)) if found else {}
    payload["evidence"] = [
        {"evidence_id": html.unescape(i), "source_code": html.unescape(s), "text": body}
        for i, s, body in _EVIDENCE.findall(user)
    ]
    return payload


def _quote(text: str) -> str:
    """證據原文開頭的一小段（逐字），作為 evidence_spans 的原句。"""
    return text.strip().splitlines()[0][:QUOTE_CHARS]


class DemoProvider:
    """依 Skill 分派的假供應商；`register` 把來源文章 id 對應到資料集中的文章。"""

    name = "demo"

    def __init__(self, dataset: Dataset, today: date) -> None:
        self.dataset = dataset
        self.today = today
        self.events = {e.key: e for e in dataset.events}
        self.series = {s.key: s for s in dataset.series}
        self.articles: dict[str, Article] = {}
        self.titles: dict[str, str] = {}
        for e in dataset.events:
            self.titles[e.title] = e.key
            self.titles[e.bulletin_title] = e.key
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "relevance-filter": self._relevance,
            "event-extractor": self._extract,
            "event-classifier": self._classify,
            "location-resolver": self._location,
            "bulletin-writer": self._bulletin,
            "event-linker": self._link,
            "evidence-verifier": self._verify,
            "risk-assessor": self._risk,
            "series-matcher": self._match_series,
            "series-analyst": self._analyze_series,
            "impact-evaluator": self._impact,
            "scenario-simulator": self._scenario,
            "trend-forecaster": self._trend,
        }

    def register(self, evidence_id: str, article: Article) -> None:
        """記錄來源文章 id（十六進位）對應的文章劇本。"""
        self.articles[evidence_id] = article

    async def generate_structured(
        self,
        messages: Sequence[Message],
        json_schema: Mapping[str, Any],
        *,
        schema_name: str,
        model: str,
        timeout: float,
        max_output_tokens: int,
    ) -> LLMResult:
        """依 schema 名稱找出 Skill，回傳該 Skill 的劇本輸出。"""
        skill = schema_name.removesuffix("_output").replace("_", "-")
        handler = self._handlers.get(skill)
        if handler is None:
            raise RuntimeError(f"示範種子沒有 {skill} 的劇本")
        output = handler(parse_payload(messages))
        return LLMResult(
            provider=self.name,
            model=model,
            text=json.dumps(output, ensure_ascii=False),
            usage=Usage(input_tokens=200, output_tokens=80),
            response_id=None,
            latency_ms=1,
        )

    # ---------- 查詢 ----------

    def _article(self, evidence_id: str) -> Article:
        try:
            return self.articles[evidence_id]
        except KeyError:
            raise RuntimeError("示範種子收到未登記的證據") from None

    def _event_of(self, evidence_ids: Sequence[str]) -> EventSpec:
        for e in evidence_ids:
            article = self.articles.get(str(e))
            if article is not None and article.event is not None:
                return self.events[article.event]
        raise RuntimeError("示範種子找不到證據所屬的事件")

    def _events_of(self, evidence_ids: Sequence[str]) -> set[str]:
        return {
            a.event
            for e in evidence_ids
            if (a := self.articles.get(str(e))) is not None and a.event is not None
        }

    # ---------- 管線（S01–S06、S13、S15） ----------

    @staticmethod
    def _base(evidence_ids: Sequence[str], reason: str) -> dict[str, Any]:
        return {
            "evidence_ids": list(evidence_ids),
            "evidence_spans": [],
            "reason": reason,
            "abstain_reason": None,
        }

    def _relevance(self, p: dict[str, Any]) -> dict[str, Any]:
        e = p["evidence"][0]["evidence_id"]
        return {
            "status": "ok",
            "is_campus_relevant": True,
            "confidence": 0.9,
            **self._base([e], "內容與校園有關。"),
        }

    def _extract(self, p: dict[str, Any]) -> dict[str, Any]:
        e = p["evidence"][0]["evidence_id"]
        article = self._article(e)
        empty = {
            "occurrence_start_time": None,
            "occurrence_end_date": None,
            "occurrence_end_time": None,
        }
        if article.event is None:
            return {
                "status": "ok",
                "is_event": False,
                "title": None,
                "occurrence_start_date": None,
                **empty,
                "location": None,
                "mentions_person": False,
                "summary": None,
                "confidence": 0.9,
                **self._base([e], "內容是詢問，不是事件。"),
            }
        event = self.events[article.event]
        return {
            "status": "ok",
            "is_event": True,
            "title": event.title,
            "occurrence_start_date": (self.today - timedelta(event.occurred_ago)).isoformat(),
            **empty,
            "location": event.location,
            "mentions_person": event.mentions_person,
            "summary": {"text": event.bulletin_summary, "evidence_ids": [e]},
            "confidence": 0.9,
            **self._base([e], "寫明事件的時間與地點。"),
        }

    def _classify(self, p: dict[str, Any]) -> dict[str, Any]:
        e = p["evidence"][0]["evidence_id"]
        event = self._event_of([e])
        return {
            "status": "ok",
            "category": event.category,
            "tags": [],
            "classification_confidence": 0.9,
            **self._base([e], "依內容分類。"),
        }

    def _location(self, p: dict[str, Any]) -> dict[str, Any]:
        name = str(p["new_location"]["name"])
        alias = self.dataset.aliases.get(name)
        target = next((c for c in p.get("candidates") or [] if c["name"] == alias), None)
        evidence = [e for e in p["evidence"] if name in e["text"]]
        if target is None or not evidence:
            return {
                "status": "abstain",
                "decision": None,
                "target_id": None,
                "confidence": 0.2,
                "evidence_ids": [],
                "evidence_spans": [],
                "reason": "資訊不足，無法判斷是否為同一地點。",
                "abstain_reason": "insufficient_evidence",
            }
        e = evidence[0]["evidence_id"]
        return {
            "status": "ok",
            "decision": "alias_of",
            "target_id": target["location_id"],
            "confidence": 0.85,
            "evidence_ids": [e],
            "evidence_spans": [{"evidence_id": e, "quote": name}],
            "reason": f"「{name}」就在「{alias}」旁邊，是同一個地方的不同說法。",
            "abstain_reason": None,
        }

    def _bulletin(self, p: dict[str, Any]) -> dict[str, Any]:
        e = p["evidence"][0]["evidence_id"]
        event = self._event_of([e])
        return {
            "status": "ok",
            "title": event.bulletin_title,
            "summary": event.bulletin_summary,
            "evidence_ids": [e],
            "reason": "依原文改寫成快報。",
            "abstain_reason": None,
        }

    def _link(self, p: dict[str, Any]) -> dict[str, Any]:
        article_ids = [str(e) for e in p["article"]["evidence_ids"]]
        candidate_ids = [str(e) for e in p["candidate"]["evidence_ids"]]
        event = self._event_of(article_ids)
        candidates = self._events_of(candidate_ids)
        if event.key in candidates:
            relation = "same_event"
        elif event.related_to in candidates:
            relation = "related_event"
        else:
            relation = "none"
        return {
            "status": "ok",
            "relation": relation,
            "confidence": 0.9,
            **self._base([*article_ids, *candidate_ids], "比對時間、地點與內容。"),
        }

    def _verify(self, p: dict[str, Any]) -> dict[str, Any]:
        rows = []
        for ev in p["evidence"]:
            article = self.articles.get(str(ev["evidence_id"]))
            if article is not None:
                rows.append((str(ev["evidence_id"]), str(ev["source_code"]), article))
        assessments = [
            Assessment(e, source, Stance(a.stance), ClaimType(a.claim)) for e, source, a in rows
        ]
        return {
            "status": "ok",
            "assessments": [
                {"evidence_id": e, "stance": a.stance, "claim_type": a.claim} for e, _, a in rows
            ],
            "suggested_status": decide(assessments).status.value,
            "confidence": 0.8,
            **self._base([e for e, _, _ in rows], "依各份證據的立場判斷。"),
        }

    def _risk(self, p: dict[str, Any]) -> dict[str, Any]:
        ids = [str(e["evidence_id"]) for e in p["evidence"]]
        event = self._event_of(ids)
        return {
            "status": "ok",
            "dimensions": {
                name: {"score": score, "uncertainty": 0.3, "reason": "依來源內容評分。"}
                for name, score in RISK_SCORES[event.risk].items()
            },
            **self._base(ids[:1], "依來源內容評估。"),
        }

    # ---------- 系列（S11、S12） ----------

    def _series_key(self, titles: Sequence[str]) -> str | None:
        keys = [self.events[self.titles[t]].series for t in titles if t in self.titles]
        common = Counter(k for k in keys if k is not None).most_common(1)
        return common[0][0] if common else None

    def _match_series(self, p: dict[str, Any]) -> dict[str, Any]:
        ids = [str(e) for e in p["event"]["evidence_ids"]]
        key = self._event_of(ids).series
        target = next(
            (
                c["series_id"]
                for c in p["candidates"]
                if key is not None and self._series_key([e["title"] for e in c["events"]]) == key
            ),
            None,
        )
        return {
            "status": "ok",
            "same_topic": target is not None,
            "target_id": target,
            "confidence": 0.9,
            **self._base(ids[:1], "比對事件與系列的主題。"),
        }

    def _codes(self, p: dict[str, Any], finding: Finding) -> list[str]:
        codes = []
        for item in p["events"]:
            key = self.titles.get(str(item["title"]))
            if finding.events is None or key in finding.events:
                codes.append(str(item["event_id"]))
        return codes

    @staticmethod
    def _evidence_of(p: dict[str, Any], codes: Sequence[str]) -> list[str]:
        wanted = set(codes)
        return [
            str(e)
            for item in p["events"]
            if item["event_id"] in wanted
            for e in item["evidence_ids"]
        ]

    def _spans(self, p: dict[str, Any], codes: Sequence[str]) -> list[dict[str, str]]:
        texts = {str(e["evidence_id"]): str(e["text"]) for e in p["evidence"]}
        for e in self._evidence_of(p, codes):
            if e in texts:
                return [{"evidence_id": e, "quote": _quote(texts[e])}]
        return []

    def _finding(self, p: dict[str, Any], finding: Finding, minimum: int) -> dict[str, Any] | None:
        codes = self._codes(p, finding)
        spans = self._spans(p, codes)
        if len(codes) < minimum or not spans:
            return None
        return {
            "description": finding.description,
            "event_ids": codes,
            "evidence_ids": self._evidence_of(p, codes)[:30],
            "evidence_spans": spans,
        }

    def _analyze_series(self, p: dict[str, Any]) -> dict[str, Any]:
        spec: SeriesSpec | None = self.series.get(
            self._series_key([str(e["title"]) for e in p["events"]]) or ""
        )
        all_ids = [str(e["evidence_id"]) for e in p["evidence"]][:30]
        if spec is None:
            return {
                "status": "abstain",
                "title": None,
                "burst_explanations": [],
                "location_groups": [],
                "pattern_status": "abstain",
                "pattern_abstain_reason": "too_few_events",
                "patterns": [],
                "causes": [],
                "measures": [],
                "evidence_ids": [],
                "reason": "事件太少，無法分析。",
                "abstain_reason": "insufficient_evidence",
            }
        bursts = []
        for b in p.get("bursts") or []:
            start = date.fromisoformat(str(b["period_start"]))
            codes = [
                str(e["event_id"])
                for e in p["events"]
                if e.get("occurrence_start_date")
                and start
                <= date.fromisoformat(str(e["occurrence_start_date"]))
                < start + timedelta(7)
            ]
            if codes:
                bursts.append(
                    {
                        "burst_id": b["burst_id"],
                        "explanation": spec.burst_explanation,
                        "event_ids": codes,
                        "evidence_ids": self._evidence_of(p, codes)[:30],
                    }
                )
        by_place: dict[str, list[str]] = {}
        for e in p["events"]:
            if e.get("location"):
                by_place.setdefault(str(e["location"]), []).append(str(e["event_id"]))
        groups = [
            {"label": place, "event_ids": codes, "reason": "發生在同一個地點。"}
            for place, codes in by_place.items()
            if len(codes) >= 2
        ][:8]
        patterns = []
        for i, f in enumerate(spec.patterns, start=1):
            found = self._finding(p, f, minimum=2)
            if found is not None:
                patterns.append({"pattern_id": f"p{i}", **found})
        causes = []
        for i, f in enumerate(spec.causes, start=1):
            found = self._finding(p, f, minimum=1)
            if found is not None:
                causes.append({"cause_id": f"c{i}", **found, "to_verify": f.to_verify or ""})
        measures = [
            {
                "action": m.action,
                "audience": m.audience,
                "horizon": m.horizon,
                "priority": m.priority,
                "kind": m.kind,
                "cause_id": m.cause,
                "responsible_unit": m.unit,
                "evidence_ids": all_ids[:1],
            }
            for m in spec.measures
        ]
        return {
            "status": "ok",
            "title": spec.title,
            "burst_explanations": bursts[:6],
            "location_groups": groups,
            "pattern_status": "ok" if patterns else "abstain",
            "pattern_abstain_reason": None if patterns else "no_pattern",
            "patterns": patterns,
            "causes": causes,
            "measures": measures,
            "evidence_ids": all_ids,
            "reason": spec.summary,
            "abstain_reason": None,
        }

    # ---------- 評估與解讀（S14、S10、S09） ----------

    def _impact(self, p: dict[str, Any]) -> dict[str, Any]:
        causes = [str(c["cause_id"]) for c in p.get("causes") or []]
        selected = [m for m in p["measures"] if m["selected"]]
        covered = sorted({str(m["cause_id"]) for m in selected if m.get("cause_id") in causes})
        unselected = [str(m["measure_id"]) for m in p["measures"] if not m["selected"]]
        return {
            "status": "ok",
            "improvement": {"low": 20, "high": 40},
            "before": "照目前的情況，總圖前車棚沒有監視器、車架也不能鎖車身，夜間竊案可能持續。",
            "after": "若比照宿舍區車棚加裝監視器並增設固定車架，下手的機會減少，件數可能下降。",
            "covered_causes": covered,
            "uncovered_causes": [c for c in causes if c not in covered],
            "overlaps": [],
            "risks": ["竊案可能再轉移到其他沒有監視器的車棚。"],
            "suggestion": {
                "measure_id": unselected[0] if unselected else None,
                "reason": "可以同時搭配，補足短期的空窗。" if unselected else None,
            },
            "evidence_ids": [str(e["evidence_id"]) for e in p["evidence"]][:3],
            "reason": "依推測原因與所選建議的對應關係估計，尚未查證。",
            "abstain_reason": None,
        }

    def _scenario(self, p: dict[str, Any]) -> dict[str, Any]:
        ids = [str(e["evidence_id"]) for e in p["evidence"]]
        return {
            "status": "ok",
            "summary": "依歷史件數與各措施的假設效果估計，加裝監視器的影響最大。",
            "assumptions": [
                {
                    "measure_id": m,
                    "statement": "假設這項措施依計畫完成並持續執行。",
                    "evidence_ids": [m],
                }
                for m in ids
            ],
            "key_driver": ids[0] if ids else None,
            "limitations": ["hypothetical_not_prediction", "independence_assumed"],
            "evidence_ids": ids,
            "reason": "依引擎結果說明各項假設。",
            "abstain_reason": None,
        }

    def _trend(self, p: dict[str, Any]) -> dict[str, Any]:
        known = {str(e["evidence_id"]) for e in p["evidence"]}
        signals = [s for s in p["series"] if s.get("anomaly")] or list(p["series"])
        explanations = []
        cited: list[str] = []
        for s in signals[:6]:
            ids = [str(e) for e in s.get("evidence_ids") or [] if str(e) in known][:12]
            cited += ids
            place = s.get("location") or "全校"
            explanations.append(
                {
                    "signal_id": s["signal_id"],
                    "summary": f"{place}近期件數高於前幾週，多則通報集中在相近的時間。",
                    "driver": "incident_cluster" if ids else "unexplained",
                    "basis": "supported" if ids else "hypothesized",
                    "evidence_ids": ids,
                }
            )
        level = str((p.get("coverage") or {}).get("level") or "partial")
        return {
            "status": "ok",
            "explanations": explanations,
            "outlook": [
                {
                    "statement": "若沒有新的改善措施，接下來幾週可能維持偏高。",
                    "signal_id": explanations[0]["signal_id"] if explanations else None,
                    "evidence_ids": [],
                }
            ],
            "coverage_level": level if level in ("full", "partial", "low") else "partial",
            "caveats": ["small_counts", "volume_not_event_count"],
            "evidence_ids": list(dict.fromkeys(cited))[:12],
            "reason": "依異常週期的事件說明。",
            "abstain_reason": None,
        }
