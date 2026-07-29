import { HealthStatus } from '../api'

// Readiness is not inferred from AGENT_MODE: the gateway may be running while
// its Chat Completions endpoint is disabled or unauthorized.
export function LocalBadge({ health }: { health: HealthStatus | null }) {
  const status = health?.agent_status.status
  const verified = status === 'verified'
  const ready = status === 'endpoint_enabled'
  const failed = status && !['mock', 'endpoint_enabled', 'verified'].includes(status)
  const label =
    status === 'mock'
      ? 'Inference: mock mode'
      : verified
        ? 'Local agent · verified'
        : ready
          ? 'Local agent · endpoint enabled'
          : status === 'endpoint_disabled'
            ? 'Local agent · chat endpoint off'
            : status === 'unauthorized'
              ? 'Local agent · unauthorized'
              : status === 'unreachable'
                ? 'Local agent · unreachable'
                : status === 'failed'
                  ? 'Local agent · error'
                  : 'Agent status…'
  return (
    <div className="flex items-center gap-2 text-xs rounded-full border border-line px-3 py-1.5">
      <span className={`h-2 w-2 rounded-full ${verified ? 'bg-accent' : failed ? 'bg-alert' : 'bg-sub'}`} />
      <span className="text-body">{label}</span>
    </div>
  )
}
