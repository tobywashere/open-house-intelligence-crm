import { API_BASE as BASE, authenticatedFetch } from './auth'

export interface NativeDirectoryReceipt {
  request_id: string
  operation: 'list_lead_directory'
  result: { total: number; offset: 0; limit: 25; leads: { id: number; name: string; status: string }[] }
}

export async function readCrmDirectory(message: string): Promise<NativeDirectoryReceipt> {
  const controller = new AbortController()
  const deadline = setTimeout(() => controller.abort(), 65_000)
  try {
    const response = await authenticatedFetch(`${BASE}/chat/directory`, {
      method: 'POST', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error?.message || 'The CRM read failed. No current result was received.')
    const r = data.result
    if (!/^[a-f0-9]{32}$/.test(data.request_id) || data.operation !== 'list_lead_directory'
      || !r || !Number.isSafeInteger(r.total) || r.total < 0 || r.offset !== 0 || r.limit !== 25
      || !Array.isArray(r.leads) || r.leads.length !== Math.min(r.total, 25)
      || r.leads.some((x: { id: number; name: string; status: string }) => !x || !Number.isSafeInteger(x.id) || x.id < 1 || typeof x.name !== 'string' || !x.name
        || !['new', 'contacted', 'meeting_booked', 'closed'].includes(x.status))
      || new Set(r.leads.map((x: { id: number }) => x.id)).size !== r.leads.length) {
      throw new Error('The CRM result could not be verified. No result is displayed.')
    }
    return data
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw new Error('The CRM read timed out. Try again.')
    if (e instanceof TypeError) throw new Error('The CRM service is unavailable. No current result was received.')
    throw e
  } finally { clearTimeout(deadline) }
}
