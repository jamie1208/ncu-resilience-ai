/*
 * 設計系統登記表（ADR-0014）。vite.config.ts 在建置時讀取，決定 `@design` 指向哪一套與載入哪些字體；
 * 本檔不得依賴瀏覽器 API。
 */

export const DESIGN_NAMES = ['minimal', 'playful'] as const
export type DesignName = (typeof DESIGN_NAMES)[number]

export const DEFAULT_DESIGN: DesignName = 'minimal'

const GOOGLE_FONTS = 'https://fonts.googleapis.com/css2?'

/** 各設計的 Google Fonts 樣式表；拉丁字體沒有繁中字形，一律加 Noto Sans TC。 */
export const FONT_STYLESHEETS: Record<DesignName, string> = {
  minimal:
    GOOGLE_FONTS +
    'family=Calistoga&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500' +
    '&family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@400&display=swap',
  playful:
    GOOGLE_FONTS +
    'family=Outfit:wght@700;800&family=Plus+Jakarta+Sans:wght@400;500;700' +
    '&family=JetBrains+Mono:wght@400;500&family=Noto+Sans+TC:wght@400;500;700;900&display=swap',
}

function isDesignName(value: string): value is DesignName {
  return (DESIGN_NAMES as readonly string[]).includes(value)
}

/** 把 `VITE_DESIGN` 轉成設計名稱；未設定用預設值，未知值拋錯讓建置失敗。 */
export function resolveDesign(value: string | undefined): DesignName {
  const trimmed = value?.trim() ?? ''
  if (trimmed === '') return DEFAULT_DESIGN
  if (isDesignName(trimmed)) return trimmed
  throw new Error(`VITE_DESIGN 必須是 ${DESIGN_NAMES.join('、')} 之一，收到「${trimmed}」`)
}
