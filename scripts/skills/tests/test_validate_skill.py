"""validate_skill.py 的測試（P2-T02）。

以 `skills/_template` 為正確範例，複製後逐項弄壞，確認每一種錯誤都被擋下。

執行：python3 -m pytest scripts/skills/tests -q（需要 pyyaml、jsonschema）
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "skills" / "validate_skill.py"
TEMPLATE = ROOT / "skills" / "_template"

_spec = importlib.util.spec_from_file_location("validate_skill", SCRIPT)
assert _spec and _spec.loader
vs = importlib.util.module_from_spec(_spec)
sys.modules["validate_skill"] = vs
_spec.loader.exec_module(vs)


# --- 工具 ----------------------------------------------------------------------------------


def copy_template(tmp_path: Path, folder: str = "_template") -> Path:
    dst = tmp_path / "skills" / folder
    shutil.copytree(TEMPLATE, dst)
    return dst


def rename_skill(skill: Path, name: str) -> None:
    md = skill / "SKILL.md"
    md.write_text(
        md.read_text(encoding="utf-8").replace("name: template", f"name: {name}", 1),
        encoding="utf-8",
    )


def edit_text(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} 不在 {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def edit_json(path: Path, fn: Callable[[dict], None]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_cases(skill: Path) -> list[dict]:
    lines = (skill / "tests" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


def write_cases(skill: Path, cases: list[dict]) -> None:
    text = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases)
    (skill / "tests" / "cases.jsonl").write_text(text, encoding="utf-8")


def errors(skill: Path) -> list[str]:
    return vs.validate_skill(skill)


def assert_blocked(skill: Path, needle: str) -> None:
    errs = errors(skill)
    assert errs, "應該被擋下，卻通過了"
    assert any(needle in e for e in errs), f"找不到含「{needle}」的錯誤：{errs}"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60
    )


# --- 正確範例 ------------------------------------------------------------------------------


def test_repo_template_passes() -> None:
    assert errors(TEMPLATE) == []


def test_copied_template_passes(tmp_path: Path) -> None:
    assert errors(copy_template(tmp_path)) == []


def test_product_skill_name_matches_folder(tmp_path: Path) -> None:
    skill = copy_template(tmp_path, "relevance-filter")
    rename_skill(skill, "relevance-filter")
    assert errors(skill) == []


def test_scripts_folder_is_optional(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    shutil.rmtree(skill / "scripts")
    assert errors(skill) == []


# --- 檔案 ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "SKILL.md",
        "schemas/input.schema.json",
        "schemas/output.schema.json",
        "tests/cases.jsonl",
        "tests/recorded",
    ],
)
def test_missing_required_file(tmp_path: Path, rel: str) -> None:
    skill = copy_template(tmp_path)
    target = skill / rel
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    assert_blocked(skill, rel)


def test_references_without_markdown(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    for p in (skill / "references").glob("*.md"):
        p.unlink()
    assert_blocked(skill, "references")


# --- 名稱與 frontmatter ---------------------------------------------------------------------


def test_name_differs_from_folder(tmp_path: Path) -> None:
    skill = copy_template(tmp_path, "relevance-filter")
    assert_blocked(skill, "資料夾")


def test_template_name_keeping_underscore(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    rename_skill(skill, "_template")
    assert_blocked(skill, "小寫")


@pytest.mark.parametrize(
    "bad",
    [
        "Relevance-Filter",
        "relevance_filter",
        "relevance--filter",
        "-relevance",
        "relevance-",
        "a" * 65,
    ],
)
def test_name_not_lowercase_hyphen(tmp_path: Path, bad: str) -> None:
    skill = copy_template(tmp_path, bad)
    rename_skill(skill, bad)
    assert_blocked(skill, "name")


def test_missing_frontmatter(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    md = skill / "SKILL.md"
    md.write_text(md.read_text(encoding="utf-8").split("---\n", 2)[2], encoding="utf-8")
    assert_blocked(skill, "frontmatter")


def test_broken_frontmatter_yaml(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_text(skill / "SKILL.md", "name: template", "name: [template")
    assert_blocked(skill, "frontmatter")


def test_description_missing(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    md = skill / "SKILL.md"
    lines = md.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.startswith("description:")]
    md.write_text("".join(kept), encoding="utf-8")
    assert_blocked(skill, "description")


def test_description_too_long(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_text(skill / "SKILL.md", "description: ", "description: " + "長" * 1025)
    assert_blocked(skill, "1024")


def test_version_not_quoted(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_text(skill / "SKILL.md", 'version: "0.1.0"', "version: 0.1")
    assert_blocked(skill, "metadata.version")


def test_version_not_semver(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_text(skill / "SKILL.md", 'version: "0.1.0"', 'version: "v1"')
    assert_blocked(skill, "metadata.version")


def test_schema_version_missing(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_text(skill / "SKILL.md", '  schema_version: "1"\n', "")
    assert_blocked(skill, "metadata.schema_version")


def test_missing_section(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_text(skill / "SKILL.md", "## 證據優先序", "## 證據")
    assert_blocked(skill, "證據優先序")


# --- schemas -------------------------------------------------------------------------------


def test_schema_not_json(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    (skill / "schemas" / "input.schema.json").write_text("{not json", encoding="utf-8")
    assert_blocked(skill, "input.schema.json")


def test_schema_invalid(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(
        skill / "schemas" / "output.schema.json",
        lambda s: s["properties"]["status"].update(type="strin"),
    )
    assert_blocked(skill, "output.schema.json")


def test_output_object_allows_additional_properties(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(skill / "schemas" / "output.schema.json", lambda s: s.pop("additionalProperties"))
    assert_blocked(skill, "additionalProperties")


def test_output_required_incomplete(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(
        skill / "schemas" / "output.schema.json", lambda s: s["required"].remove("confidence")
    )
    assert_blocked(skill, "required")


def test_output_uses_one_of(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(
        skill / "schemas" / "output.schema.json",
        lambda s: s["properties"]["reason"].update(oneOf=[{"type": "string"}]),
    )
    assert_blocked(skill, "oneOf")


def test_output_missing_common_field(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)

    def drop(s: dict) -> None:
        s["properties"].pop("abstain_reason")
        s["required"].remove("abstain_reason")

    edit_json(skill / "schemas" / "output.schema.json", drop)
    assert_blocked(skill, "abstain_reason")


def test_output_status_without_abstain(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(
        skill / "schemas" / "output.schema.json",
        lambda s: s["properties"]["status"].update(enum=["ok"]),
    )
    assert_blocked(skill, "abstain")


def test_input_without_evidence(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)

    def rename(s: dict) -> None:
        s["properties"]["articles"] = s["properties"].pop("evidence")
        s["required"] = ["articles"]

    edit_json(skill / "schemas" / "input.schema.json", rename)
    assert_blocked(skill, "evidence")


def test_input_evidence_missing_text(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(
        skill / "schemas" / "input.schema.json",
        lambda s: s["properties"]["evidence"]["items"]["required"].remove("text"),
    )
    assert_blocked(skill, "text")


# --- golden cases --------------------------------------------------------------------------


def test_fewer_than_20_cases(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    write_cases(skill, read_cases(skill)[:19])
    assert_blocked(skill, "20")


def test_no_adversarial_case(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    for c in cases:
        if c["type"] == "adversarial":
            c["type"] = "boundary"
    write_cases(skill, cases)
    assert_blocked(skill, "adversarial")


def test_missing_boundary_type(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    for c in cases:
        if c["type"] == "boundary":
            c["type"] = "normal"
    write_cases(skill, cases)
    assert_blocked(skill, "boundary")


def test_no_test_split(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    for c in cases:
        c["split"] = "dev"
    write_cases(skill, cases)
    assert_blocked(skill, "split")


def test_unknown_split(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[0]["split"] = "train"
    write_cases(skill, cases)
    assert_blocked(skill, "split")


def test_duplicate_case_id(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[1]["id"] = cases[0]["id"]
    write_cases(skill, cases)
    assert_blocked(skill, "重複")


def test_case_id_format(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    old = cases[0]["id"]
    cases[0]["id"] = "case1"
    write_cases(skill, cases)
    recorded = skill / "tests" / "recorded"
    (recorded / f"{old}.json").rename(recorded / "case1.json")
    assert_blocked(skill, "case1")


def test_case_id_type_mismatch(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[0]["type"] = "boundary"  # id 是 normal-001
    write_cases(skill, cases)
    assert_blocked(skill, cases[0]["id"])


def test_case_line_not_json(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    with (skill / "tests" / "cases.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{broken\n")
    assert_blocked(skill, "第 21 行")


@pytest.mark.parametrize("field", ["input", "expected", "must_not"])
def test_case_missing_field(tmp_path: Path, field: str) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[0].pop(field)
    write_cases(skill, cases)
    assert_blocked(skill, field)


def test_case_empty_must_not(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[0]["must_not"] = []
    write_cases(skill, cases)
    assert_blocked(skill, "must_not")


def test_case_unknown_must_not_op(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[0]["must_not"] = [{"path": "status", "op": "regex", "value": "ok"}]
    write_cases(skill, cases)
    assert_blocked(skill, "op")


def test_case_must_not_missing_path(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[0]["must_not"] = [{"op": "eq", "value": "ok"}]
    write_cases(skill, cases)
    assert_blocked(skill, "path")


def test_case_must_not_bad_regex(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[0]["must_not"] = [{"op": "matches", "value": "([a-z"}]
    write_cases(skill, cases)
    assert_blocked(skill, "正規表示式")


def test_case_input_violates_schema(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[0]["input"]["evidence"][0].pop("text")
    write_cases(skill, cases)
    assert_blocked(skill, cases[0]["id"])


def test_case_expected_unknown_field(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    cases = read_cases(skill)
    cases[0]["expected"]["severity"] = "high"
    write_cases(skill, cases)
    assert_blocked(skill, "severity")


# --- 錄製回應 ------------------------------------------------------------------------------


def test_recorded_missing(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    (skill / "tests" / "recorded" / "normal-001.json").unlink()
    assert_blocked(skill, "normal-001")


def test_recorded_version_mismatch(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(
        skill / "tests" / "recorded" / "normal-001.json", lambda r: r.update(skill_version="0.0.9")
    )
    assert_blocked(skill, "skill_version")


def test_recorded_case_id_mismatch(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(
        skill / "tests" / "recorded" / "normal-001.json", lambda r: r.update(case_id="normal-002")
    )
    assert_blocked(skill, "case_id")


def test_recorded_missing_model(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(skill / "tests" / "recorded" / "normal-001.json", lambda r: r.pop("model"))
    assert_blocked(skill, "model")


def test_recorded_output_violates_schema(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_json(
        skill / "tests" / "recorded" / "normal-001.json",
        lambda r: r["output"].update(confidence=1.5),
    )
    assert_blocked(skill, "normal-001")


def test_recorded_without_case(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    shutil.copy(
        skill / "tests" / "recorded" / "normal-001.json",
        skill / "tests" / "recorded" / "normal-099.json",
    )
    assert_blocked(skill, "normal-099")


def test_reports_several_errors_at_once(tmp_path: Path) -> None:
    skill = copy_template(tmp_path)
    edit_text(skill / "SKILL.md", "## 證據優先序", "## 證據")
    write_cases(skill, read_cases(skill)[:19])
    errs = errors(skill)
    assert any("證據優先序" in e for e in errs) and any("20" in e for e in errs)


# --- 命令列 --------------------------------------------------------------------------------


def test_cli_passes_on_repo_skills_glob() -> None:
    # CI 執行 `validate_skill.py skills/*`：README.md 之類的檔案略過
    args = [str(p) for p in sorted((ROOT / "skills").iterdir())]
    res = run(*args)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "_template" in res.stdout


def test_cli_accepts_skills_root(tmp_path: Path) -> None:
    copy_template(tmp_path)
    res = run(str(tmp_path / "skills"))
    assert res.returncode == 0, res.stdout + res.stderr


def test_cli_fails_and_names_broken_skill(tmp_path: Path) -> None:
    good = copy_template(tmp_path)
    bad = copy_template(tmp_path, "event-classifier")
    res = run(str(good), str(bad))
    assert res.returncode == 1
    assert "event-classifier" in res.stdout


def test_cli_without_any_skill(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    res = run(str(tmp_path / "README.md"))
    assert res.returncode == 2
