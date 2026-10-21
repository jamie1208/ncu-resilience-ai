/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type Plugin } from 'vite'

import { FONT_STYLESHEETS, resolveDesign, type DesignName } from './src/design/registry.ts'

/** 把所選設計的 Google Fonts 樣式表注入 index.html（ADR-0014）。 */
function designFonts(design: DesignName): Plugin {
  return {
    name: 'ncu-design-fonts',
    transformIndexHtml: () => [
      {
        tag: 'link',
        attrs: { rel: 'stylesheet', href: FONT_STYLESHEETS[design] },
        injectTo: 'head',
      },
    ],
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // VITE_DESIGN=minimal|playful 在建置時決定 `@design` 指向哪一套元件庫；未知值直接失敗
  const design = resolveDesign(loadEnv(mode, process.cwd(), 'VITE_').VITE_DESIGN)

  return {
    plugins: [react(), tailwindcss(), designFonts(design)],
    resolve: {
      alias: {
        '@design': fileURLToPath(new URL(`./src/design/${design}`, import.meta.url)),
      },
    },
    server: {
      // 開發時把 API 轉到本機 FastAPI（P0-T04 後由 Docker Compose 提供）
      proxy: { '/api': 'http://localhost:8000' },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  }
})
