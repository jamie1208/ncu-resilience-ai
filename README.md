# NCU Resilience AI

國立中央大學 AI 校園韌性與智慧事件決策系統（個人使用的專題，非校方官方系統，不對外公開）。

> 本系統不取代校安中心、110、119 等正式緊急通報管道。

## 文件

| 文件 | 用途 |
| --- | --- |
| [規格書 v1.0](docs/requirements/NCU_Resilience_AI_System_Plan_v1.pdf) | 需求基線 |
| [執行計畫](docs/plan/execution-plan.md) | 6 階段、62 任務的檔案清單與驗證方式 |
| [目標架構](docs/architecture/project-structure.md) | 完成後的目錄結構與分層 |
| [AGENTS.md](AGENTS.md) | AI coding agent 的開發規範 |

## 開始開發

需求：Git、Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node.js 20+、Docker Desktop、VS Code + Claude Code 擴充套件。

```bash
git clone <repo-url> && cd ncu-resilience-ai
pip install pyyaml            # tracker 需要
python .claude/skills/ncu-resilience-tracker/scripts/tracker.py status
```

在 VS Code 開啟本資料夾，打開 Claude Code，依序執行：

```text
/review              # 看目前階段與下一個任務
/task P0-T01         # 執行任務
/finish P0-T01       # 你確認後結案
```

服務啟動方式會在 P0-T04 完成後補上（`make up`）。

## 狀態

進度見 `docs/progress/state.yaml`，或執行 `/review`。
