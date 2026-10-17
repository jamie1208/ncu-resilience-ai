# frontend

NCU Resilience AI 前端：React + TypeScript + Vite，樣式採 Tailwind CSS 與 Framer Motion（ADR-0003），設計規範見 `.claude/skills/ncu-frontend-design/`。

```bash
npm install
npm run dev          # http://localhost:5173，/api 轉到 http://localhost:8000
npm run lint && npm run test && npm run build
```

| 路徑 | 內容 |
| --- | --- |
| `src/styles/index.css` | 設計 token（`@theme`）、字體堆疊、reduced-motion 規則 |
| `src/lib/cn.ts` | `clsx` + `tailwind-merge`（認得自訂 token） |
| `src/api/client.ts` | `{data, meta, error}` 用戶端與 `ApiError` |
| `src/components/` | 共用基礎元件（cva） |
| `src/public/` | 公開前端（P3-T04） |
| `src/admin/` | 管理後台（P3-T05） |

環境變數：`VITE_API_BASE_URL`（選填，預設同源）。前端不放任何金鑰。
