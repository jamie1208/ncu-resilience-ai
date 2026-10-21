---
name: ncu-frontend-design
description: NCU Resilience AI 前端的兩套設計系統（ADR-0014，建置時以 VITE_DESIGN 選擇）：Minimalist Modern（Electric Blue 漸層、Calistoga + Inter + JetBrains Mono、反轉深色區塊、細緻動態）與 Playful Geometric（Memphis 風：奶油底、2px 粗框、硬陰影、膠囊、Outfit + Plus Jakarta Sans、彈跳動態）。在設計、實作或重構 frontend/ 下任何頁面、元件、樣式、設計 token 時使用，包括 P3-T11 雙設計系統、P3-T05 管理前端、RelationGraph、Scenario 等前端任務。
metadata:
  version: "1.1.0"
  project: NCU Resilience AI
---

# NCU Resilience AI 前端設計系統

本 skill 規範前端的視覺與互動風格。前端有兩套設計系統，動手寫任何前端程式碼前，必須先讀你要改的那一套：

- Minimalist Modern：`references/design-system.md`，實作在 `frontend/src/design/minimal/`
- Playful Geometric：`references/playful-geometric.md`，實作在 `frontend/src/design/playful/`

## 雙設計系統架構（ADR-0014）

- 頁面（`src/admin/` 等）只從 `@design` 匯入元件，不得直接匯入 `design/minimal` 或 `design/playful`。
- `VITE_DESIGN=minimal|playful`（預設 minimal）在建置時決定 `@design` 指向哪一套，以及載入哪些字體（`src/design/registry.ts`）。
- 兩套元件庫實作同一份介面（`src/design/contract.ts` 的 `DesignLibrary`），由 `contract.test.ts` 與型別檢查強制執行。新增元件時兩套都要做，並加進 contract。
- 頁面只能使用 contract 的 `REQUIRED_TOKENS` 對應的色彩與字體 class；各設計專屬的 token（例如 playful 的 `secondary`、`tertiary`、`shadow-pop`）只能出現在該設計的元件內部。
- 共用的 base 與 reduced-motion 放在 `src/styles/base.css`，各設計的 `styles.css` 都要引入它。
- 開發：`npm run dev` 或 `npm run dev:playful`；建置：`npm run build` 或 `npm run build:playful`。

## 角色

以資深前端工程師、UI/UX 設計師、視覺設計與字體專家的角度工作：把設計系統整合進既有 codebase，做到視覺一致、可維護、符合技術棧慣例。

## 工作流程

1. **先建立現況模型**（不要直接寫碼）：
   - 技術棧：React + TypeScript + Vite（見 AGENTS.md）；確認 `frontend/package.json` 實際已安裝的套件。
   - 既有設計 token（色彩、間距、字體、圓角、陰影）、全域樣式、工具類別。
   - 元件架構與命名慣例（`src/design/<設計>/`、`src/admin/`）。
   - 限制：既有 CSS、bundle 大小、效能。
2. **釐清範圍**：若任務定義未說清楚，問使用者是要（a）重新設計特定元件／頁面、（b）把既有元件重構到新系統、還是（c）用新風格建立新頁面。
3. **提出精簡計畫**，優先順序：集中設計 token → 元件可重用與組合 → 減少重複與一次性樣式 → 長期可維護與清楚命名。
4. **實作**時沿用既有資料夾結構、命名、樣式寫法與元件模式；簡短說明架構或設計決策的理由。

## 本專案的套用規則（優先於設計系統原文）

1. **任務範圍**：仍遵守 AGENTS.md，一次一個任務、只改執行計畫列出的檔案。設計 token、字體載入等共用檔案若不在清單內，先說明理由並取得同意。
2. **樣式與動畫技術**：依 ADR-0003 採用 Tailwind CSS、Framer Motion、`cva` + `tailwind-merge`（搭配 `clsx`）。設計 token 定義為 CSS custom properties 並對應到 Tailwind 主題；基礎元件在 `src/design/<設計>/` 自建，不安裝整套元件庫。要加入 ADR-0003 以外的 UI／動畫套件需另寫 ADR。
3. **這是校園韌性／事件資訊系統，不是行銷頁**：
   - 原文的 Pricing、Testimonials、Final CTA 等行銷區塊不適用；保留其視覺語言（token、section label、反轉區塊、卡片、漸層強調），套用到事件列表、事件詳情、報告、管理工作台。
   - 緊急、高風險資訊（110／119 提示、警報、風險等級）以**清楚與可讀**為最優先：不加漂浮、旋轉等裝飾動畫，不用漸層文字呈現關鍵數字或警告文字。
   - 風險等級、事件狀態等語意色要另外定義（例如 danger／warning／success），不得只靠 Electric Blue 區分，也不能只靠顏色傳達（搭配文字或圖示）。
   - About 頁的「非官方系統」免責聲明必須清楚可見，不可被裝飾性設計弱化。
4. **管理後台密度較高**：`src/admin/` 以資訊密度與操作效率為主，縮小區塊間距（不用 `py-28`～`py-44`），裝飾元素（光暈、旋轉環、漂浮卡片）只用在公開前端首頁。
5. **中文字體**：Calistoga、Inter、JetBrains Mono、Outfit、Plus Jakarta Sans 都沒有繁體中文字形（Playful 的字體堆疊見其 reference）。字體堆疊必須加上中文 fallback，例如：
   - 標題：`"Calistoga", "Noto Serif TC", Georgia, serif`
   - 內文：`"Inter", "Noto Sans TC", "PingFang TC", system-ui, sans-serif`
   - 等寬：`"JetBrains Mono", "Noto Sans Mono CJK TC", monospace`
   - 中文 section label 不要套 `tracking-[0.15em]` 與全大寫，只對英文標籤使用。
6. **無障礙**：所有文字需符合 WCAG AA；所有持續性動畫都要支援 `prefers-reduced-motion`；觸控目標至少 44px（Playful 至少 48px）；focus ring 一律可見。
7. **時間顯示**：畫面上的時間一律轉成 Asia/Taipei 顯示（後端存 UTC）。

## 檢查清單（交付前）

- [ ] 色彩、字體、陰影、圓角都來自集中定義的 token，沒有寫死的色碼。
- [ ] 頁面只從 `@design` 匯入元件；新元件兩套都有實作並列入 contract。
- [ ] 375px 手機寬度無水平捲動，裝飾元素在小螢幕隱藏。
- [ ] `prefers-reduced-motion` 下沒有持續性動畫。
- [ ] 中文在各字體堆疊下正常顯示。
- [ ] `cd frontend && npm run lint && npm run test && npm run build && npm run build:playful` 通過。
