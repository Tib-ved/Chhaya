// Which dataset the screens read: the live one (the default) or the fixed demo heat day.
// Remembered per browser; a request in demo mode carries `mode=demo`, a live one is left as it was.
export type Mode = 'live' | 'demo'

const KEY = 'chhaya.mode'
/** Where Ask Chhaya keeps the conversation it resumes (sessionStorage). */
export const ASK_SESSION_KEY = 'chhaya_ask_session'

export function getMode(): Mode {
  try { return localStorage.getItem(KEY) === 'demo' ? 'demo' : 'live' } catch { return 'live' }
}

export function setMode(mode: Mode) {
  try { localStorage.setItem(KEY, mode) } catch { /* private window: the choice lasts for this page only */ }
}

/** The path with `mode=demo` added in demo mode; unchanged in live mode. */
export function withMode(path: string): string {
  return getMode() === 'demo' ? path + (path.includes('?') ? '&' : '?') + 'mode=demo' : path
}

/** Open `path` on the demo dataset, where a visitor's test call is kept: on live data, switch and load the
 * page afresh (as the switch does) and return true, so the caller stops its own navigation. */
export function openInDemo(path: string): boolean {
  if (getMode() === 'demo') return false
  switchMode('demo')
  window.location.assign(path)
  return true
}

/** Change the mode; on a real change, Ask Chhaya starts a new conversation rather than resuming one held on the other dataset. */
export function switchMode(mode: Mode) {
  if (mode === getMode()) return
  setMode(mode)
  try { sessionStorage.removeItem(ASK_SESSION_KEY); localStorage.removeItem(ASK_SESSION_KEY) } catch { /* private window */ }
}
