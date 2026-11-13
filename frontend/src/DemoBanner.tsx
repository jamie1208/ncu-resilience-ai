/**
 * 示範站橫幅（P9-T01，ADR-0022）：以 `VITE_DEMO=1` 建置的示範環境，在後台與公開站頂端標示
 * 內容是虛構的示範資料。只使用 contract 的共用 token，兩套設計系統都適用。
 */

import { isDemo } from './lib/demo'

export function DemoBanner({ demo = isDemo(import.meta.env.VITE_DEMO) }: { demo?: boolean }) {
  if (!demo) return null
  return (
    <div
      role="note"
      aria-label="示範資料"
      className="border-b border-warning bg-warning-muted px-4 py-2 text-center text-sm text-foreground"
    >
      <strong className="font-semibold">示範資料</strong>
      ：本站內容皆為虛構，用來展示系統功能，與真實校園事件無關。
    </div>
  )
}
