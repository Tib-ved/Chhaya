const VISITOR_KEY = 'chhaya_visitor_id'
const TEAM_SECRET_KEY = 'chhaya_team_secret'

let inMemoryVisitorId: string | null = null

export function getVisitorId(): string {
  if (inMemoryVisitorId) return inMemoryVisitorId

  try {
    const stored = localStorage.getItem(VISITOR_KEY)
    if (stored) {
      inMemoryVisitorId = stored
      return stored
    }
    const created = crypto.randomUUID()
    localStorage.setItem(VISITOR_KEY, created)
    inMemoryVisitorId = created
    return created
  } catch {
    // If localStorage is blocked or restricted
    if (!inMemoryVisitorId) {
      inMemoryVisitorId = crypto.randomUUID()
    }
    return inMemoryVisitorId
  }
}

export function getTeamSecret(): string | null {
  try {
    return sessionStorage.getItem(TEAM_SECRET_KEY)
  } catch {
    return null
  }
}

export function setTeamSecret(secret: string): void {
  try {
    sessionStorage.setItem(TEAM_SECRET_KEY, secret)
  } catch {
    // ignore
  }
}

export function clearTeamSecret(): void {
  try {
    sessionStorage.removeItem(TEAM_SECRET_KEY)
  } catch {
    // ignore
  }
}
