import { getTeamSecret, getVisitorId } from './visitor'
import { withMode } from './mode'

export class ApiError extends Error {
  status: number
  reason?: string
  retryAfterSeconds?: number

  constructor(message: string, status: number, reason?: string, retryAfterSeconds?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.reason = reason
    this.retryAfterSeconds = retryAfterSeconds
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const visitorId = getVisitorId()
  const teamSecret = getTeamSecret()

  const headers = new Headers(options.headers || {})
  headers.set('X-Visitor', visitorId)

  if (teamSecret) {
    headers.set('X-Internal-Secret', teamSecret)
  }

  if (options.body && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(withMode(path), {
    credentials: 'include',
    ...options,
    headers,
  })

  if (!response.ok) {
    let detail = `Request failed: ${response.status} ${response.statusText}`
    let reason: string | undefined
    let retryAfter: number | undefined

    try {
      const data = await response.json()
      if (data.detail) detail = data.detail
      if (data.reason) reason = data.reason
      if (data.retry_after_s) retryAfter = data.retry_after_s
    } catch {
      // not json
    }

    throw new ApiError(detail, response.status, reason, retryAfter)
  }

  return response.json()
}

export function startVisitorPhoneCall(cardId: string, language: string, phone: string) {
  return request<{ call_id: string }>('/api/visitor/phone-calls', {
    method: 'POST',
    body: JSON.stringify({ card_id: cardId, language, phone }),
  })
}
