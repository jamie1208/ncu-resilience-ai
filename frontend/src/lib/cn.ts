import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

/** 讓 tailwind-merge 認得 `styles/index.css` 中自訂的 token，才能正確判斷衝突。 */
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      shadow: ['accent', 'accent-lg'],
      font: ['display'],
    },
  },
})

/** 組合 className：clsx 處理條件，tailwind-merge 讓後面的類別覆蓋衝突的前者。 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
