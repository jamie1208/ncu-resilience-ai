/**
 * 後端 API 用戶端：解析統一回應格式 `{data, meta, error}`（規格 §10.1）。
 * 不在前端保存任何金鑰；管理者驗證之後改用 HttpOnly Cookie（同源送出）。
 */

/** 規格 §10.1 定義的錯誤碼，加上前端自己判定的三種。 */
export type ApiErrorCode =
  | 'VALIDATION_ERROR'
  | 'UNAUTHORIZED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'RATE_LIMITED'
  | 'CONFLICT'
  | 'MODEL_UNAVAILABLE'
  | 'SERVER_ERROR'
  | 'NETWORK_ERROR'
  | 'INVALID_RESPONSE'

export interface ApiMeta {
  request_id: string | null
  timestamp: string
  [key: string]: unknown
}

export interface ApiSuccess<T> {
  data: T
  meta: ApiMeta
}

interface ApiErrorBody {
  code: string
  message: string
}

interface Envelope<T> {
  data: T
  meta: ApiMeta
  error: ApiErrorBody | null
}

export class ApiError extends Error {
  readonly code: ApiErrorCode | string
  readonly status: number
  readonly requestId: string | null

  constructor(code: ApiErrorCode | string, message: string, status: number, requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.requestId = requestId
  }
}

const STATUS_CODES: Record<number, ApiErrorCode> = {
  400: 'VALIDATION_ERROR',
  401: 'UNAUTHORIZED',
  403: 'FORBIDDEN',
  404: 'NOT_FOUND',
  409: 'CONFLICT',
  422: 'VALIDATION_ERROR',
  429: 'RATE_LIMITED',
}

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

function isEnvelope(body: unknown): body is Envelope<unknown> {
  return typeof body === 'object' && body !== null && 'data' in body && 'meta' in body && 'error' in body
}

function codeForStatus(status: number): ApiErrorCode {
  return STATUS_CODES[status] ?? (status >= 500 ? 'SERVER_ERROR' : 'INVALID_RESPONSE')
}

/** 送出請求並回傳 `{data, meta}`；任何失敗都拋出 `ApiError`。 */
export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<ApiSuccess<T>> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')

  let res: Response
  try {
    res = await fetch(`${BASE_URL}${path}`, { credentials: 'same-origin', ...init, headers })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError('NETWORK_ERROR', '無法連線到伺服器', 0)
  }

  const body: unknown = await res.json().catch(() => null)

  if (isEnvelope(body)) {
    if (body.error) {
      throw new ApiError(body.error.code, body.error.message, res.status, body.meta.request_id)
    }
    if (res.ok) return { data: body.data as T, meta: body.meta }
  }
  if (!res.ok) {
    throw new ApiError(codeForStatus(res.status), `請求失敗（HTTP ${res.status}）`, res.status)
  }
  throw new ApiError('INVALID_RESPONSE', '伺服器回應格式不正確', res.status)
}

/** GET 請求的捷徑。 */
export function apiGet<T>(path: string, init: Omit<RequestInit, 'method' | 'body'> = {}) {
  return apiRequest<T>(path, { ...init, method: 'GET' })
}
