/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { REQUIRED_TOKENS, type DesignLibrary } from './contract'
import * as minimal from './minimal'
import * as playful from './playful'
import type { DesignName } from './registry'

// 型別檢查（tsc -b）強制兩套元件庫實作同一份介面
const libraries = { minimal, playful } satisfies Record<DesignName, DesignLibrary>
// vitest 設定 css: false，`?raw` 匯入會是空字串，所以直接讀檔（jsdom 的 URL 不是 file scheme，改用路徑）
const readCss = (name: DesignName) =>
  readFileSync(join(import.meta.dirname, name, 'styles.css'), 'utf8')
const stylesheets: Record<DesignName, string> = {
  minimal: readCss('minimal'),
  playful: readCss('playful'),
}

describe('設計 contract', () => {
  it('兩套元件庫匯出相同的元件名稱', () => {
    expect(Object.keys(libraries.playful).sort()).toEqual(Object.keys(libraries.minimal).sort())
  })

  it.each(Object.keys(stylesheets) as DesignName[])('%s 定義全部必要語意 token', (name) => {
    const css = stylesheets[name]
    const missing = REQUIRED_TOKENS.filter((token) => !new RegExp(`${token}\\s*:`).test(css))
    expect(missing).toEqual([])
  })

  it.each(Object.keys(stylesheets) as DesignName[])(
    '%s 引入 Tailwind 與共用 base（含 reduced-motion）',
    (name) => {
      const css = stylesheets[name]
      expect(css).toContain("@import 'tailwindcss'")
      expect(css).toContain("@import '../../styles/base.css'")
    },
  )
})
