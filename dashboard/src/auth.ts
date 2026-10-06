export type AuthMode = 'local' | 'token' | 'capabilities'
export type AuthRole = 'human' | 'agent' | null

export interface AuthStatus {
  mode: AuthMode
  role: AuthRole
  workflow_mode?: 'native' | 'standard'
}

export const API_BASE =
  import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000/api' : '/api')

let token = ''
let generation = 0
const lockListeners = new Set<() => void>()

export const authGeneration = () => generation

function tokenHeaders(value: string, initial?: HeadersInit): Headers {
  const headers = new Headers(initial)
  if (value) headers.set('X-API-Token', value)
  return headers
}

/** Install a token only if no newer lock/unlock action happened meanwhile. */
export function commitAuthToken(value: string, expectedGeneration: number): boolean {
  if (generation !== expectedGeneration) return false
  token = value
  generation += 1
  return true
}

export function lockAuthentication(): void {
  token = ''
  generation += 1
  for (const listener of lockListeners) listener()
}

export function onAuthenticationLocked(listener: () => void): () => void {
  lockListeners.add(listener)
  return () => lockListeners.delete(listener)
}

export async function authStatus(candidate = token): Promise<AuthStatus> {
  const response = await fetch(`${API_BASE}/auth/status`, {
    headers: tokenHeaders(candidate),
  })
  if (!response.ok) throw new Error('Authentication status is unavailable.')
  const status = await response.json()
  if (
    !status
    || !['local', 'token', 'capabilities'].includes(status.mode)
    || ![null, 'human', 'agent'].includes(status.role)
    || ('workflow_mode' in status && !['native', 'standard'].includes(status.workflow_mode))
  ) throw new Error('Authentication status is invalid.')
  return status as AuthStatus
}

/** Fetch with the current in-memory credential and reset only its own 401. */
export async function authenticatedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const requestToken = token
  const requestGeneration = generation
  const response = await fetch(input, {
    ...init,
    headers: tokenHeaders(requestToken, init.headers),
  })
  if (response.status === 401 && generation === requestGeneration && token === requestToken) {
    lockAuthentication()
  }
  return response
}
