/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 後端 API 位址；未設定時使用同源（開發時由 Vite proxy 轉到 FastAPI）。 */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
