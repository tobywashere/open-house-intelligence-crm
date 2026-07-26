import { Metrics } from '../api'

// The pitch badge: everything runs on the GB10, nothing goes to the cloud.
export function LocalBadge({ metrics }: { metrics: Metrics | null }) {
  const mode = metrics?.agent_mode ?? '…'
  const live = mode === 'openclaw'
  return (
    <div className="flex items-center gap-2 text-xs rounded-full border border-line px-3 py-1.5">
      <span className={`h-2 w-2 rounded-full ${live ? 'bg-accent' : 'bg-sub'}`} />
      <span className="text-body">
        {live ? 'Qwen 3.6 35B-A3B · Local on Dell GB10' : `Inference: ${mode} mode`}
      </span>
      <span className="text-sub/60">|</span>
      <span className="text-sub">Cloud LLM requests: {metrics?.cloud_llm_requests ?? 0}</span>
    </div>
  )
}
