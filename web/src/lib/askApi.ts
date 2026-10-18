// Ask Chhaya: /api/ask. Sessions are plain JSON; an answer streams as server-sent events, read with fetch
// (EventSource cannot POST).
import { ApiError, request } from './api'
import { getTeamSecret, getVisitorId } from './visitor'
import { withMode } from './mode'

export type Focus = { state: string; district_id: string; ward_id: string }
export type ChartSpec = {
  type: 'bar' | 'stacked_bar' | 'grouped_bar' | 'line' | 'donut'
  title: string; x_label: string; y_label: string
  series: { name: string; points: { label: string; value: number }[] }[]
}
export type TableSpec = { title: string; columns: string[]; rows: (string | number | null)[][] }
export type Visual = { kind: 'chart'; id: string; spec: ChartSpec } | { kind: 'table'; id: string; spec: TableSpec }
export type AskMessage = {
  role: 'user' | 'assistant'; text: string; visuals: Visual[]
  status?: string; case_ids?: string[]; kind?: 'answer' | 'refused'; error?: string; streaming?: boolean
}
export type SessionRow = { session_id: string; title: string; updated_at: string }
export type SessionView = { session_id: string; title: string; focus: Partial<Focus>; messages: AskMessage[] }

export type StreamEvent =
  | { event: 'status'; data: { label: string } }
  | { event: 'delta'; data: { text: string } }
  | { event: 'chart'; data: { id: string; spec: ChartSpec } }
  | { event: 'table'; data: { id: string; spec: TableSpec } }
  | { event: 'sources'; data: { case_ids: string[]; call_ids: string[] } }
  | { event: 'title'; data: { title: string } }
  | { event: 'done'; data: { data_as_of: string; kind: 'answer' | 'refused' } }
  | { event: 'error'; data: { code: string; message: string } }

export const createSession = (focus: Focus) =>
  request<{ session_id: string }>('/api/ask/sessions', { method: 'POST', body: JSON.stringify({ focus }) })
export const listSessions = () => request<SessionRow[]>('/api/ask/sessions')
export const getSession = (id: string) => request<SessionView>(`/api/ask/sessions/${encodeURIComponent(id)}`)

/** Split a growing text buffer into complete `event:`/`data:` frames; returns the frames and what is left. */
export function parseFrames(buffer: string): { frames: StreamEvent[]; rest: string } {
  const blocks = buffer.split('\n\n')
  const rest = blocks.pop() ?? ''
  const frames: StreamEvent[] = []
  for (const block of blocks) {
    let event = '', data = ''
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7)
      else if (line.startsWith('data: ')) data += line.slice(6)
    }
    if (event && data) {
      try { frames.push({ event, data: JSON.parse(data) } as StreamEvent) } catch { /* a broken frame is skipped */ }
    }
  }
  return { frames, rest }
}

export async function sendMessage(id: string, text: string, focus: Focus, onEvent: (e: StreamEvent) => void, signal?: AbortSignal) {
  const headers = new Headers({ 'Content-Type': 'application/json', 'X-Visitor': getVisitorId(), Accept: 'text/event-stream' })
  const secret = getTeamSecret()
  if (secret) headers.set('X-Internal-Secret', secret)
  const response = await fetch(withMode(`/api/ask/sessions/${encodeURIComponent(id)}/messages`), {
    method: 'POST', credentials: 'include', headers, body: JSON.stringify({ text, focus }), signal,
  })
  if (!response.ok || !response.body) {
    let detail = `Request failed: ${response.status}`
    let reason: string | undefined
    try { const body = await response.json(); if (body.detail) detail = body.detail; reason = body.reason } catch { /* not json */ }
    throw new ApiError(detail, response.status, reason)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    const { frames, rest } = parseFrames(buffer)
    buffer = rest
    frames.forEach(onEvent)
  }
  if (buffer.trim()) parseFrames(buffer + '\n\n').frames.forEach(onEvent)
}
