import { useEffect, useState } from 'react'

let pushToast: ((msg: string) => void) | null = null
let nextId = 1

export const toast = (msg: string) => pushToast?.(msg)

export function Toasts() {
  const [items, setItems] = useState<{ id: number; msg: string }[]>([])

  useEffect(() => {
    pushToast = (msg) => {
      const id = nextId++
      setItems((t) => [...t, { id, msg }])
      setTimeout(() => setItems((t) => t.filter((x) => x.id !== id)), 5000)
    }
    return () => {
      pushToast = null
    }
  }, [])

  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 space-y-2">
      {items.map((t) => (
        <div
          key={t.id}
          className="rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm shadow-xl"
        >
          {t.msg}
        </div>
      ))}
    </div>
  )
}
