/** 公開網站的事件類別圖示與配色（只用語意 token，兩套設計通用）。 */

type Tone = 'accent' | 'info' | 'success' | 'warning' | 'danger' | 'muted'

/** 類別的圖示路徑（24×24，線條）與配色；配色只是輔助，類別一律同時寫出文字。 */
const CATEGORY_STYLES: Record<string, { tone: Tone; path: string }> = {
  facility: { tone: 'warning', path: 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z' },
  weather: { tone: 'accent', path: 'M7 17h10a4 4 0 0 0 0-8 6 6 0 0 0-11 2 3 3 0 0 0 1 6z' },
  security: { tone: 'info', path: 'M7 11V7a5 5 0 0 1 10 0v4M5 11h14v10H5zM12 15.5v1.5' },
  traffic: { tone: 'success', path: 'M5 17V9l2-4h10l2 4v8M5 13h14M8 17h.01M16 17h.01' },
  health: { tone: 'success', path: 'M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6z' },
  fire: { tone: 'danger', path: 'M12 3c1 4 6 6 6 11a6 6 0 0 1-12 0c0-3 2-5 3-7 1 2 2 3 3 3 0-3-1-5 0-7z' },
  academic: { tone: 'info', path: 'M3 9l9-5 9 5-9 5zM7 11v5c3 2 7 2 10 0v-5' },
  infosec: { tone: 'muted', path: 'M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z' },
}

/** 每個色調的淡色底（卡片色塊）、文字色與實心色（色塊、長條）。 */
const TONE_CLASSES: Record<Tone, { tint: string; ink: string; solid: string }> = {
  accent: { tint: 'bg-accent/10', ink: 'text-accent', solid: 'bg-accent' },
  info: { tint: 'bg-info-muted', ink: 'text-info', solid: 'bg-info' },
  success: { tint: 'bg-success-muted', ink: 'text-success', solid: 'bg-success' },
  warning: { tint: 'bg-warning-muted', ink: 'text-warning', solid: 'bg-warning' },
  danger: { tint: 'bg-danger-muted', ink: 'text-danger', solid: 'bg-danger' },
  muted: { tint: 'bg-muted', ink: 'text-muted-foreground', solid: 'bg-muted-foreground' },
}

/** 類別的圖示路徑與色調；未知類別用十字圖示與中性色。 */
export function categoryStyle(type: string): { tone: Tone; path: string } {
  return CATEGORY_STYLES[type] ?? { tone: 'muted' as Tone, path: 'M12 4v16M4 12h16' }
}

/** 類別的配色 class（淡色底、文字色、實心色）。 */
export function categoryTone(type: string): { tint: string; ink: string; solid: string } {
  return TONE_CLASSES[categoryStyle(type).tone]
}
