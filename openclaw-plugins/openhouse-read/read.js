// A small receipt adapter: the native tool, not generated text, publishes facts.
const PREFIX = '/openhouse/read-receipts/';
const TTL = 90_000;
const MAX_PENDING = 128;
const MAX_BYTES = 2_000_000;
const STATUSES = new Set(['new', 'contacted', 'meeting_booked', 'closed']);
export const parameters = {
  type: 'object', additionalProperties: false, required: ['operation', 'arguments'],
  properties: {
    operation: { type: 'string', enum: ['list_lead_directory'] },
    arguments: { type: 'object', properties: {}, additionalProperties: false },
  },
};

async function limitedJson(response) {
  const reader = response.body.getReader(); const chunks = []; let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      length += value.length;
      if (length > MAX_BYTES) throw new Error('CRM directory is too large');
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } finally { await reader.cancel(); }
}

export function definition({ fetchImpl = fetch, now = Date.now } = {}) {
  const pending = new Map();
  const prune = () => { for (const [id, row] of pending) if (row.expires <= now()) pending.delete(id); };
  return {
    id: 'openhouse-read', name: 'OpenHouse read-only directory',
    register(api) {
      const { agentId, crmApiUrl } = api.pluginConfig;
      const base = new URL(crmApiUrl);
      if (base.protocol !== 'http:' || !['localhost','127.0.0.1','[::1]'].includes(base.hostname)
          || base.username || base.password || base.search || base.hash || base.pathname.replace(/\/$/, '') !== '/api') {
        throw new Error('CRM reads require a loopback /api URL');
      }
      api.registerHttpRoute({
        path: PREFIX, match: 'prefix', auth: 'gateway',
        async handler(req, res) {
          prune();
          const path = new URL(req.url, 'http://localhost').pathname;
          const id = path.slice(PREFIX.length);
          const send = (status, value) => {
            res.statusCode = status; res.setHeader('Content-Type', 'application/json');
            res.setHeader('Cache-Control', 'no-store'); res.end(JSON.stringify(value)); return true;
          };
          if (!/^[a-f0-9]{32}$/.test(id)) return send(400, {error:'invalid_request_id'});
          if (req.method === 'POST') {
            if (pending.has(id)) return send(409, {error:'duplicate_request'});
            if (pending.size >= MAX_PENDING) return send(429, {error:'busy'});
            pending.set(id, {expires:now()+TTL, state:'pending'});
            return send(201, {request_id:id});
          }
          if (req.method === 'DELETE') { pending.delete(id); return send(200, {deleted:true}); }
          if (req.method !== 'GET') return send(405, {error:'method_not_allowed'});
          const row = pending.get(id); pending.delete(id); // one-shot, including failure
          if (!row || row.state !== 'done') return send(404, {error:'missing_result'});
          return send(200, row.receipt);
        },
      });
      api.registerTool(context => ({
        name: 'openhouse_crm',
        description: 'Read the current CRM lead directory and its total count. Call list_lead_directory with an empty arguments object. Read-only.',
        parameters,
        async execute(_callId, args) {
          prune();
          const expectedPrefix = `agent:${agentId}:openai-user:ohi-read-`;
          const key = context.sessionKey;
          const id = typeof key === 'string' && key.startsWith(expectedPrefix) ? key.slice(expectedPrefix.length) : '';
          const row = pending.get(id);
          if (context.agentId !== agentId || !row || row.state !== 'pending') throw new Error('No current read request');
          if (!args || Object.keys(args).length !== 2 || args.operation !== 'list_lead_directory'
              || !args.arguments || typeof args.arguments !== 'object' || Array.isArray(args.arguments)
              || Object.keys(args.arguments).length) throw new Error('Only an unfiltered directory read is permitted');
          row.state = 'running';
          try {
            const headers = { 'X-Actor':'agent', 'X-OpenHouse-Read-Request':id };
            const crmToken = process.env.OHI_AGENT_API_TOKEN || process.env.OHI_API_TOKEN;
            if (crmToken) headers['X-API-Token'] = crmToken;
            const response = await fetchImpl(crmApiUrl.replace(/\/$/, '')+'/leads', {
              method:'GET', headers, redirect:'error', signal:AbortSignal.timeout(10_000),
            });
            if (!response.ok) throw new Error('CRM backend unavailable');
            // The existing /leads API returns the full unpaginated list. Count
            // that list BEFORE slicing the model/display page, as PR #7 did.
            const rows = await limitedJson(response);
            if (!Array.isArray(rows) || rows.some(r => !r || !Number.isSafeInteger(r.id) || r.id < 1
                || typeof r.name !== 'string' || !r.name || r.name.length > 1000 || !STATUSES.has(r.status))
                || new Set(rows.map(r => r.id)).size !== rows.length) throw new Error('Invalid directory');
            const result = { total:rows.length, offset:0, limit:25,
              leads:rows.slice(0,25).map(({id,name,status}) => ({id,name,status})) };
            if (pending.get(id) !== row || row.expires <= now()) throw new Error('Read request expired');
            row.receipt = {request_id:id, operation:'list_lead_directory', result}; row.state = 'done';
            return {content:[{type:'text',text:JSON.stringify(result)}],details:result};
          } catch {
            if (pending.get(id) === row) row.state = 'failed';
            throw new Error('CRM read failed');
          }
        },
      }), {name:'openhouse_crm'});
      api.on('gateway_stop', () => pending.clear());
    },
  };
}
