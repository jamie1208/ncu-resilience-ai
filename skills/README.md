# skills — 產品 AI Skills（S01–S10）

系統執行時由後端 Skill Runner 載入，格式遵守 Agent Skills 規格（https://agentskills.io/specification）。開發輔助用的 skills 放在 `.claude/skills/`，不要放這裡。

每個 Skill 依 `docs/plan/execution-plan.md` 的「Skill 開發標準循環」建立：

```text
skills/<skill-name>/
  SKILL.md                    # frontmatter: name, description, metadata.version, metadata.schema_version
  schemas/input.schema.json
  schemas/output.schema.json
  references/*.md
  scripts/                    # 可選，確定性計算
  tests/cases.jsonl           # ≥ 20 筆，含 "type": "adversarial"
  tests/recorded/             # CI 用錄製回應
```

| ID | Skill | 任務 |
| --- | --- | --- |
| — | _template | P2-T01 |
| S01 | relevance-filter | P2-T06 |
| S02 | event-extractor | P2-T07 |
| S03 | event-classifier | P2-T08 |
| S04 | event-linker | P2-T09 |
| S05 | evidence-verifier | P2-T10 |
| S06 | risk-assessor | P2-T11 |
| S07 | root-cause-analyst | P2-T12 |
| S08 | solution-planner | P2-T12 |
| S09 | trend-forecaster | P5-T01 |
| S10 | scenario-simulator | P5-T02 |
