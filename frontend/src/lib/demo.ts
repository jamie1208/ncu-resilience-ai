/** 建置設定 `VITE_DEMO` 是否代表示範站（P9-T01，ADR-0022）。 */
export function isDemo(value: string | undefined): boolean {
  return value === '1' || value === 'true'
}
