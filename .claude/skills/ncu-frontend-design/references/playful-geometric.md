# Playful Geometric 設計系統

使用者於 2026-10-21 提供（ADR-0014）。實作位置：`frontend/src/design/playful/`。下方「本專案調整」一節優先於原文。

## 設計理念

**Playful Geometric** 是冷調企業極簡風的反面，用**樂觀、清楚、有觸感的趣味**建立情感連結。

核心概念是**「穩定的格線，狂野的裝飾」**：內容本身（文字、表單）放在乾淨易讀的區域，周圍則充滿動態與形狀。參考 80 年代的 **Memphis Group**，但為現代螢幕整理過：去掉混亂，保留能量。

- **氛圍**：友善、有觸感、普普、有活力。像遊樂場或整理得很好的貼紙本，讓人想點。
- **視覺特徵**：
  - **基本形狀**：圓、三角、方、膠囊、波浪線，當背景、遮罩或圖示底。
  - **硬陰影**：無模糊的偏移陰影，像貼紙或剪紙。
  - **圖樣填充**：圓點、格線、斜線條紋。
  - **混合圓角**：全圓角與直角混用，做出葉形或不對稱形狀。

## Token

### 色彩（淺色模式）

| token | 值 | 用途 |
| --- | --- | --- |
| background | `#FFFDF5` | 奶油色紙張底 |
| foreground | `#1E293B` | Slate 800，比黑色柔和 |
| muted | `#F1F5F9` | Slate 100 |
| muted-foreground | `#64748B` | Slate 500 |
| accent | `#8B5CF6` | Vivid Violet，主品牌色（本專案實心底改用 `#7C3AED`，見下方調整） |
| accent-foreground | `#FFFFFF` | |
| secondary | `#F472B6` | Hot Pink |
| tertiary | `#FBBF24` | Amber |
| quaternary | `#34D399` | Emerald |
| border | `#E2E8F0` | Slate 200 |
| input / card | `#FFFFFF` | |
| ring | `#8B5CF6` | |

規則：主要操作用 `accent`；`secondary`、`tertiary`、`quaternary` 輪流用在裝飾形狀、圖示與強調字上，做出「彩紙」效果。

### 字體

- 標題：`"Outfit"`，700／800。幾何無襯線，字母帶圓角，感覺友善。
- 內文：`"Plus Jakarta Sans"`，400／500。
- 字級比例 1.25（Major Third）。

### 圓角與邊框

- `radius-sm` 8px、`radius-md` 16px、`radius-lg` 24px、`radius-full` 9999px。
- 預設邊框寬度 2px（粗框）。
- 特殊形狀：對話框（`rounded-tl-2xl rounded-tr-2xl rounded-br-2xl rounded-bl-none`）、拱形（`rounded-t-full rounded-b-none`）。

### 陰影（硬陰影，無模糊）

- 預設 `4px 4px 0 0 #1E293B`
- hover `6px 6px 0 0 #1E293B`（浮起）
- active `2px 2px 0 0 #1E293B`（按下）

### 紋理

- 點狀格線：小圓點嚴格排列。
- 波浪線：SVG path，當區段分隔或標題底線。
- 彩紙：小三角、小圓絕對定位在主要內容後方。

## 元件

- **Primary 按鈕（糖果按鈕）**：accent 底、白字 700、膠囊、2px `#1E293B` 框、4px 硬陰影；hover 往左上移 2px、陰影變 6px；active 往右下移 2px、陰影變 2px；可放一個白色圓底的 ArrowRight 圖示。
- **Secondary 按鈕**：透明底、foreground 字、2px 深色框、膠囊、無陰影；hover 填 tertiary 黃色。
- **貼紙卡**：白底、2px 深色框、`rounded-xl`、`8px 8px 0 #E2E8F0` 陰影（featured 用 `#F472B6` 粉紅）；hover 時旋轉 -1 度並放大 1.02；標題用粗體 Outfit；圖示放在一個浮動圓圈裡，一半在上框外、一半在框內。
- **輸入框**：白底、2px `#CBD5E1` 框、`rounded-lg`；focus 時框線變 accent，加上 `4px 4px 0 accent` 硬陰影；label 粗體、全大寫、小字、寬字距。

## 版面

- 容器寬 `max-w-6xl`；區段間距 `py-24`，空間寬鬆但用圖樣填滿。
- 12 欄邏輯，但分成大區塊（6/6、4/4/4）。

## 動態

- 整體感覺：有彈性、會回彈、好玩。
- hover：`transition-all duration-300`，曲線 `cubic-bezier(0.34, 1.56, 0.64, 1)`（overshoot）。
- 進場：不只淡入，用 scale 0→1 加回彈的 pop 效果。
- 圖示 hover 抖動：`rotate 0 → 3deg → -3deg → 0`。

## 圖示

- Lucide，stroke 2.5，圓角線端與接點。
- 圖示一定要放在形狀裡，不單獨漂浮。例如勾勾要放在綠色圓圈裡。

## 響應式

- 手機版：所有區塊改為堆疊，硬陰影縮為 2px，橫向波浪線改成直向分隔，按鈕高度至少 48px，隱藏可能蓋住文字的複雜漂浮形狀。

## 無障礙

- 文字使用 slate-800 配白色或米白底。
- 不只靠顏色傳達資訊，要搭配形狀與文字。
- 遵守 `prefers-reduced-motion`，在該設定下停用 bounce 與 wiggle。
- focus 狀態要高對比（粗色框加硬陰影）。

## 本專案調整（優先於原文，ADR-0014）

1. **對比**：白字配 `#8B5CF6` 只有約 4.2:1，未達 AA。實心底（按鈕、徽章）的 `accent` 改用 `#7C3AED`，對比約 5.7:1。`#8B5CF6` 保留給 `accent-bright`、`ring` 和裝飾。
2. **語意色**：原文沒有風險語意色，所以沿用 Minimalist Modern 的 danger／warning／success／info。info 改用 `#7C3AED`。彩紙色（secondary、tertiary、quaternary）不得用來表示風險等級。
3. **緊急資訊**：風險等級、警報、110／119 提示不加 bounce、wiggle、pop-in 或旋轉，也不放在會搖擺的卡片裡。
4. **管理後台**：區段間距縮小，不用 `py-24`。彩紙、波浪線等裝飾只放在頁首或空狀態，不放在資料表格或事件詳情旁邊。卡片只有可點擊的才加 hover 搖擺（`interactive`）。
5. **中文**：Outfit 和 Plus Jakarta Sans 沒有繁中字形，接 `Noto Sans TC`。中文標籤不使用全大寫與寬字距。
6. **圖示**：目前尚未安裝 lucide-react。第一個需要圖示的任務要先說明理由並加入依賴。
7. **不做的區塊**：原文的 Hero、Pricing、Marquee 是行銷用版面，本系統不採用。
