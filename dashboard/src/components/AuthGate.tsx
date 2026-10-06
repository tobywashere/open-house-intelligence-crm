import { FormEvent, ReactNode, useEffect, useState } from 'react'
import {
  authGeneration,
  authStatus,
  commitAuthToken,
  onAuthenticationLocked,
} from '../auth'

type Phase = 'checking' | 'locked' | 'unlocked'

export function AuthGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>('checking')
  const [candidate, setCandidate] = useState('')
  const [error, setError] = useState('')

  useEffect(() => onAuthenticationLocked(() => {
    setCandidate('')
    setError('')
    setPhase('locked')
  }), [])

  useEffect(() => {
    let active = true
    const startedAt = authGeneration()
    authStatus('')
      .then((status) => {
        if (!active) return
        if (status.role === 'human' && commitAuthToken('', startedAt)) setPhase('unlocked')
        else setPhase('locked')
      })
      .catch(() => {
        if (active) {
          setError('The CRM authentication service is unavailable.')
          setPhase('locked')
        }
      })
    return () => { active = false }
  }, [])

  const unlock = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    const startedAt = authGeneration()
    setPhase('checking')
    try {
      const status = await authStatus(candidate)
      if (status.role === 'agent') {
        setError('A human credential is required to unlock the dashboard.')
        setPhase('locked')
        return
      }
      if (status.role !== 'human') {
        setError('Invalid credential.')
        setPhase('locked')
        return
      }
      if (commitAuthToken(candidate, startedAt)) setPhase('unlocked')
    } catch {
      setError('The CRM authentication service is unavailable.')
      setPhase('locked')
    }
  }

  if (phase === 'unlocked') return <>{children}</>

  return (
    <main className="min-h-screen bg-bg text-ink flex items-center justify-center p-6">
      <form
        onSubmit={unlock}
        className="w-full max-w-sm rounded-xl border border-tile bg-surface p-6 shadow-xl"
      >
        <h1 className="text-xl font-semibold">Unlock Open House Intelligence</h1>
        <p className="mt-2 text-sm text-sub">
          Enter the human API credential for this CRM. It stays in memory only.
        </p>
        <label className="mt-5 block text-sm text-body" htmlFor="api-token">API token</label>
        <input
          id="api-token"
          type="password"
          autoComplete="current-password"
          value={candidate}
          onChange={(event) => setCandidate(event.target.value)}
          disabled={phase === 'checking'}
          className="mt-2 w-full rounded-md border border-line bg-bg px-3 py-2 text-ink outline-none focus:border-accent"
        />
        {error && <p role="alert" className="mt-3 text-sm text-alert">{error}</p>}
        <button
          type="submit"
          disabled={phase === 'checking'}
          className="mt-5 w-full rounded-md bg-accent px-4 py-2 font-medium text-[#0b0f19] disabled:opacity-60"
        >
          {phase === 'checking' ? 'Checking…' : 'Unlock'}
        </button>
      </form>
    </main>
  )
}
