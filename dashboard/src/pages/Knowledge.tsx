import { useEffect, useRef, useState } from 'react'
import { api, ApiError, KnowledgeDoc, KnowledgeHit } from '../api'
import { toast } from '../components/Toast'
import { Skeleton } from '../components/Skeleton'

// Knowledge corpus management: upload a markdown doc, see what is indexed, and
// search it. The search box is the point of the page — it is how an operator
// confirms a doc they just added is actually retrievable, rather than trusting
// that it landed. Chunk counts come from the live index for the same reason.

const fmtBytes = (n: number) =>
  n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`

// FileReader gives "data:...;base64,XXXX" — the API wants just the payload.
const toBase64 = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })

export function Knowledge() {
  const [docs, setDocs] = useState<KnowledgeDoc[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null)
  const [searching, setSearching] = useState(false)
  const filePicker = useRef<HTMLInputElement>(null)

  const load = () =>
    api
      .knowledgeDocs()
      .then(setDocs)
      .catch(() => {})
      .finally(() => setLoaded(true))

  useEffect(() => {
    load()
  }, [])

  const upload = async (file: File) => {
    setBusy(true)
    try {
      const doc = await api.uploadKnowledgeDoc(file.name, await toBase64(file))
      toast(`✓ ${doc.name} indexed — ${doc.chunks} section${doc.chunks === 1 ? '' : 's'}`)
      load()
    } catch (e) {
      // the backend's detail is the useful part here (too large, not UTF-8,
      // not markdown) — surface it rather than a generic failure
      toast(`⚠ ${e instanceof ApiError ? e.detail ?? e.message : 'Upload failed'}`)
    } finally {
      setBusy(false)
      if (filePicker.current) filePicker.current.value = ''
    }
  }

  const remove = async (name: string) => {
    setBusy(true)
    try {
      await api.deleteKnowledgeDoc(name)
      toast(`✓ ${name} removed`)
      // a deleted doc's hits would linger on screen and look retrievable
      setHits(null)
      load()
    } catch (e) {
      toast(`⚠ ${e instanceof ApiError ? e.detail ?? e.message : 'Delete failed'}`)
    } finally {
      setBusy(false)
    }
  }

  const search = async () => {
    const q = query.trim()
    if (!q) return
    setSearching(true)
    try {
      setHits(await api.knowledgeSearch(q))
    } catch {
      toast('⚠ Search failed — is the backend running?')
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="max-w-4xl space-y-4">
      <section className="rounded-xl border border-tile bg-surface p-4">
        <h2 className="text-sm font-semibold text-sub mb-1">Knowledge documents</h2>
        <p className="text-xs text-sub/70 mb-3">
          Markdown the agent can quote from. Headings become searchable sections, so
          write the terms you would search for into the headings themselves.
        </p>
        <input
          ref={filePicker}
          type="file"
          accept=".md,text/markdown"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) upload(file)
          }}
          className="block w-full text-xs text-sub file:mr-3 file:rounded-full file:border-0
                     file:bg-accent/15 file:px-4 file:py-1.5 file:text-xs file:text-accent
                     hover:file:bg-accent/25 file:transition-colors disabled:opacity-50"
        />
      </section>

      <section className="rounded-xl border border-tile bg-surface p-4">
        {!loaded ? (
          <Skeleton className="h-24" />
        ) : docs.length === 0 ? (
          <p className="text-sm text-sub/70">
            No documents indexed yet. Upload a <code>.md</code> file above.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-sub/70">
                <th className="pb-2 font-medium">Document</th>
                <th className="pb-2 font-medium">Sections</th>
                <th className="pb-2 font-medium">Size</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.name} className="border-t border-tile">
                  <td className="py-2 font-medium">{d.name}</td>
                  <td className="py-2 text-sub">
                    {/* 0 sections means present but unindexed — say so plainly */}
                    {d.chunks === 0 ? <span className="text-alert">not indexed</span> : d.chunks}
                  </td>
                  <td className="py-2 text-sub">{fmtBytes(d.bytes)}</td>
                  <td className="py-2 text-right">
                    <button
                      onClick={() => remove(d.name)}
                      disabled={busy}
                      className="text-xs text-sub/70 hover:text-alert transition-colors disabled:opacity-40"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="rounded-xl border border-tile bg-surface p-4">
        <h2 className="text-sm font-semibold text-sub mb-3">Test retrieval</h2>
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            placeholder="Ask the way a user would — e.g. “what is our refund window?”"
            className="flex-1 rounded-lg border border-line bg-tile px-3 py-2 text-sm
                       placeholder:text-sub/50 focus:border-accent/60 focus:outline-none"
          />
          <button
            onClick={search}
            disabled={searching || !query.trim()}
            className="rounded-lg bg-accent/90 hover:bg-accent px-4 py-2 text-sm font-medium
                       text-bg transition-colors disabled:opacity-40"
          >
            {searching ? 'Searching…' : 'Search'}
          </button>
        </div>

        {hits !== null && (
          <div className="mt-3 space-y-2">
            {hits.length === 0 ? (
              <p className="text-sm text-sub/70">
                No sections matched. Retrieval ignores queries built only from words that
                are common across the whole corpus — try the specific term you expect.
              </p>
            ) : (
              hits.map((h, i) => (
                <div key={`${h.doc}-${h.breadcrumb}-${i}`} className="rounded-lg border border-tile bg-tile p-3">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-sm font-medium">{h.breadcrumb || h.heading}</span>
                    <span className="shrink-0 text-xs text-sub/70">
                      {h.doc} · {h.score.toFixed(2)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-sub line-clamp-3">{h.text}</p>
                </div>
              ))
            )}
          </div>
        )}
      </section>
    </div>
  )
}
