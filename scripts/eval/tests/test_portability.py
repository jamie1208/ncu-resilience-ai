"""portability.py 的測試（P7-T04）：回覆解析與 stream-json 整理。

執行：cd backend && uv run pytest ../scripts/eval/tests -q（需要後端環境）
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "eval" / "portability.py"

_spec = importlib.util.spec_from_file_location("portability", SCRIPT)
assert _spec and _spec.loader
pt = importlib.util.module_from_spec(_spec)
sys.modules["portability"] = pt
_spec.loader.exec_module(pt)


def test_extract_json_strict() -> None:
    assert pt.extract_json('{"status": "ok"}') == ({"status": "ok"}, "strict")


def test_extract_json_fenced() -> None:
    assert pt.extract_json('```json\n{"status": "ok"}\n```') == ({"status": "ok"}, "fenced")


def test_extract_json_with_surrounding_text() -> None:
    text = '結果如下：\n{"status": "abstain", "abstain_reason": "insufficient_evidence"}\n以上。'
    output, how = pt.extract_json(text)
    assert how == "extracted"
    assert output == {"status": "abstain", "abstain_reason": "insufficient_evidence"}


def test_extract_json_failed() -> None:
    assert pt.extract_json("無法判斷") == (None, "failed")
    assert pt.extract_json("[1, 2]") == (None, "failed")
    assert pt.extract_json("") == (None, "failed")


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj, ensure_ascii=False)


def test_parse_stream_detects_skill_and_result() -> None:
    lines = [
        _line({"type": "system", "subtype": "init", "model": "claude-sonnet-x"}),
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Skill", "input": {"skill": "event-linker"}},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "x"}},
                    ]
                },
            }
        ),
        "不是 JSON 的行",
        _line(
            {
                "type": "result",
                "num_turns": 3,
                "total_cost_usd": 0.05,
                "duration_ms": 9000,
                "result": '{"status": "ok"}',
                "is_error": False,
            }
        ),
    ]
    info = pt.parse_stream(lines, "event-linker")
    assert info["model"] == "claude-sonnet-x"
    assert info["skill_invoked"] is True
    assert info["tools"] == ["Skill", "Read"]
    assert info["num_turns"] == 3
    assert info["result_text"] == '{"status": "ok"}'


def test_parse_stream_other_skill_is_not_invoked() -> None:
    call = {"type": "tool_use", "name": "Skill", "input": {"skill": "event-classifier"}}
    lines = [_line({"type": "assistant", "message": {"content": [call]}})]
    info = pt.parse_stream(lines, "event-linker")
    assert info["skill_invoked"] is False
    assert info["result_text"] is None
