---
description: 依執行計畫執行一個任務，例如 /task P0-T02
argument-hint: <任務ID，例如 P1-T03>
---

執行任務 $ARGUMENTS。

開始前：
1. 讀 AGENTS.md、docs/progress/state.yaml，以及 docs/plan/execution-plan.md 中 $ARGUMENTS 的完整定義。
2. 用 ncu-resilience-tracker skill 執行 `status`，確認 $ARGUMENTS 的前置任務皆為 done。若未滿足，停止並告訴我缺什麼、建議改做哪個任務。
3. 若此任務標記「需你決定」，先列出需要我決定的事項與選項，等我回答後才開始。
4. 用 tracker 把 $ARGUMENTS 標為 in_progress，建立分支 `feat/$ARGUMENTS-<簡短描述>`（repo 尚未 git init 時先略過）。

要求：
- 只建立／修改任務列出的檔案；需要額外檔案時先說明理由。
- 先寫測試再實作；任務的所有驗證指令必須通過。
- 不得修改既有測試來遷就實作；不得把金鑰寫進程式碼。
- 遇到計畫與規格書衝突、或需要擴大範圍時，停下來問我。

完成後：
- 執行 tracker 的 `record $ARGUMENTS` 產生驗收紀錄並補齊所有段落。
- 不要自行標記 done；回報：變更檔案、測試結果、已知限制、需要我決定的事項。我確認後會執行 /finish $ARGUMENTS。
