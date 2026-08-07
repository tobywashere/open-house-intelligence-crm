import { HealthStatus } from '../api'

// Readiness is not inferred from AGENT_MODE: the gateway may be running while
// its Chat Completions endpoint is disabled or unauthorized.
export function LocalBadge({ health }: { health: HealthStatus | null }) {
  const status = health?.agent_status.status
  const verified = status === 'crm_verified'
  const failed = status && !['mock', 'endpoint_enabled', 'chat_verified', 'crm_verified'].includes(status)
  const label =
    status === 'crm_verified' ? 'CRM agent · verified' :
    status === 'chat_verified' ? 'Chat works · CRM not verified' :
    status === 'degraded' ? 'CRM agent · degraded' :
    status === 'endpoint_enabled' ? 'OpenClaw · endpoint enabled' :
    status === 'endpoint_disabled' ? 'OpenClaw · chat endpoint off' :
    status === 'unauthorized' ? 'OpenClaw · unauthorized' :
    status === 'unreachable' ? 'OpenClaw · unreachable' :
    status === 'failed' ? 'OpenClaw · error' :
    status === 'mock' ? 'Inference · mock mode' :
    'Agent status…'
  return (
    <div className="flex items-center gap-2 text-xs rounded-full border border-line px-3 py-1.5">
      <span className={`h-2 w-2 rounded-full ${verified ? 'bg-accent' : failed ? 'bg-alert' : 'bg-sub'}`} />
      <span className="text-body">{label}</span>
    </div>
  )
}
