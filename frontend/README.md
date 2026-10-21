# frontend

NCU Resilience AI 前端：React + TypeScript + Vite，樣式採 Tailwind CSS 與 Framer Motion（ADR-0003），設計規範見 `.claude/skills/ncu-frontend-design/`。

```bash
npm install
npm run dev          # http://localhost:5173，/api 轉到 http://localhost:8000
npm run lint && npm run test && npm run build && npm run build:playful
npm run e2e          # Playwright 管理流程（API 以 page.route 模擬）；e2e:playful 用另一套設計
```

| 路徑 | 內容 |
| --- | --- |
| `src/styles/base.css` | 兩套設計共用的 base 與 reduced-motion 規則 |
| `src/design/` | 雙設計系統（ADR-0014）：`contract.ts`、`minimal/`、`playful/`，頁面只從 `@design` 匯入 |
| `src/lib/cn.ts` | `clsx` + `tailwind-merge`（認得自訂 token） |
| `src/api/client.ts` | `{data, meta, error}` 用戶端與 `ApiError` |
| `src/admin/` | 管理前端（唯一的前端，ADR-0008）：登入、事件工作台、通知中心、Skills、運維 |
| `e2e/` | Playwright 管理流程測試 |

環境變數：`VITE_API_BASE_URL`（選填，預設同源）。前端不放任何金鑰。
