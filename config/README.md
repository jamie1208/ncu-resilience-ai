# config

政策與門檻設定，皆帶版本號，變更需寫 ADR 並由使用者決定。

- `risk-policy.yaml`：五維風險權重、分級、critical_override（P2-T11）
- `notification-policy.yaml`：個人通知規則（P3-T07）：通知門檻、依查證狀態的呈現方式、疑似指控關鍵字
- `llm.yaml`：模型名稱、價格、逾時與費用上限（P2-T03）。模型名稱與費用上限只放在設定中，不寫進程式。
