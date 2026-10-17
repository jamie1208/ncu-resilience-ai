# ADR-0003：前端樣式採用 Tailwind CSS 與 Framer Motion

- 狀態：已採納
- 日期：2026-10-17

## 背景

前端設計系統定為「Minimalist Modern」（見 `.claude/skills/ncu-frontend-design/`）：Electric Blue 漸層、Calistoga + Inter + JetBrains Mono、反轉深色區塊、進場與持續性動畫。設計系統原文以 Tailwind CSS 工具類別、`cva` + `tailwind-merge` 元件變體、Framer Motion 動畫撰寫。AGENTS.md 原技術棧的前端只列 React + TypeScript + Vite，未定樣式與動畫方案。

## 決策

1. **Tailwind CSS**：設計 token（色彩、字體、陰影、圓角）集中定義為 CSS custom properties，並對應到 Tailwind 主題；元件與頁面以工具類別撰寫，不寫死色碼。
2. **`class-variance-authority`（cva）+ `tailwind-merge`（搭配 `clsx`）**：Button、Card、Input、Badge 等基礎元件以 shadcn 風格的 API 在 `frontend/src/components/` 自建，不安裝整套元件庫。
3. **Framer Motion**：進場動畫、stagger 與持續性動畫統一使用；所有持續性動畫必須遵守 `prefers-reduced-motion`（`useReducedMotion` 或 `MotionConfig reducedMotion="user"`）。
4. 套件在 P0-T03 前端骨架時安裝與設定；版本由 `package-lock.json` 鎖定。

## 考慮過的替代方案

- **CSS Modules + CSS custom properties**：零額外依賴，但設計系統原文的工具類別需全部轉寫，變體與狀態組合容易產生重複樣式。
- **CSS-in-JS（styled-components、Emotion）**：執行期成本較高，且與設計系統原文寫法差距更大。
- **純 CSS 動畫**：可涵蓋 hover 與簡單 keyframes，但 viewport 進場、stagger 與 reduced-motion 整合需自行實作；簡單 hover 效果仍可用 CSS transition。

## 影響

- 前端依賴增加 `tailwindcss`、`framer-motion`、`class-variance-authority`、`tailwind-merge`、`clsx`。
- Framer Motion 增加 bundle 體積；管理後台以靜態樣式為主，裝飾性動畫限公開前端首頁，必要時以 lazy import 拆分。
- 緊急資訊、警報與風險等級不使用裝飾動畫與漸層文字（見 skill 的套用規則）。
