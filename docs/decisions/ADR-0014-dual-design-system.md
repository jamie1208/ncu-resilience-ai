# ADR-0014：雙設計系統（Minimalist Modern／Playful Geometric），建置時選擇

- 狀態：已採納
- 日期：2026-10-21
- 決定者：使用者本人（架構、切換方式、計畫安排）；實作細節由 AI 決定並記錄於此

## 背景

ADR-0003 只定義一套設計系統（Minimalist Modern）。使用者希望另做一套「Playful Geometric」風格（Memphis 風：奶油底色、2px 粗框、無模糊硬陰影、膠囊圓角、彈跳動態、幾何裝飾），並在之後的前端任務同時開發兩套。執行計畫原本把 P3-T05 寫成「唯一的前端」，因此需要調整計畫。

## 決策

1. **共用邏輯，兩套元件庫**（使用者決定）：API client、hooks、狀態、頁面流程共用；`frontend/src/design/minimal/` 與 `frontend/src/design/playful/` 各自實作元件與 token。兩者必須符合 `frontend/src/design/contract.ts` 的同一份元件介面（型別檢查強制）與同一組必要語意 token（測試強制）。
2. **建置時選擇**（使用者決定）：`VITE_DESIGN=minimal|playful`，預設 `minimal`；`vite.config.ts` 把 `@design` 別名指向對應目錄，並注入該設計的字體樣式表。未知值直接讓建置失敗。執行期不能切換，bundle 只含一套。
3. **計畫**（使用者決定）：新增 P3-T11 雙設計系統，P3-T05 依賴它。P3-T04 的 ID 已因 ADR-0008 刪除，不能重複使用，所以新任務編號為 P3-T11。
4. 頁面只能從 `@design` 匯入元件，不得直接匯入 `design/minimal` 或 `design/playful`。頁面可使用的 Tailwind 色彩與字體只有 contract 列出的語意 token；Playful 專屬的 `secondary`、`tertiary`、`quaternary` 只在 playful 元件內部使用。
5. **安全與可及性（AI 決定）**：
   - Playful 規範沒有風險語意色，因此沿用 danger／warning／success／info 四組語意色，對白字都達到 WCAG AA。風險等級與緊急資訊在兩套設計中都不得只靠顏色區分，也不加彈跳、抖動或其他裝飾動畫。
   - 彈跳、抖動、進場 pop 只用在裝飾元素和一般互動回饋上，遵守 `prefers-reduced-motion`，共用的 `styles/base.css` 會全域停用這些動畫。
   - 規範的 accent `#8B5CF6` 配白字只有約 4.2:1，未達 AA。實心底改用 `#7C3AED`（約 5.7:1），`#8B5CF6` 保留給 focus ring 與裝飾（`accent-bright`）。
   - Outfit 與 Plus Jakarta Sans 沒有繁中字形，接 Noto Sans TC 作為 fallback。
   - lucide-react 暫不安裝；第一個需要圖示的任務再加入依賴並說明理由。
   - 在手機寬度，硬陰影縮為 2px。
6. 元件測試對兩套都跑（直接用路徑匯入，不經 `@design`）；CI 兩套都要建置成功。

## 考慮過的替代方案

- **同一個 App、雙主題 token**：維護成本最低，但結構差異（硬陰影、粗框、裝飾形狀、動態曲線）都得硬塞進 token，Playful 的個性會被壓平。使用者選擇讓兩套元件庫各自實作。
- **兩個獨立前端專案**：最自由，但 API、hooks、測試和 CI 都要重複一份。
- **執行期切換**：方便並排比較，但兩套 CSS 和字體都會進 bundle。使用者選擇建置時決定。

## 影響

- `src/components/` 搬到 `src/design/minimal/`，`src/styles/index.css` 拆成共用的 `styles/base.css` 和各設計自己的 `styles.css`。
- 往後每個新元件都要在兩套各實作一次並通過 contract 檢查，前端工作量增加。
- `npm run dev:playful`、`npm run build:playful` 和 Docker 建置參數 `VITE_DESIGN` 可以產出 Playful 版本。
- ADR-0003 的工具選擇（Tailwind、cva、Framer Motion）不變。
