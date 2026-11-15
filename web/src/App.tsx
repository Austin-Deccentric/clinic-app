import { useEffect, useRef, useState, type FormEvent } from 'react'

type Slot = { id: string; provider: string; start: string; end: string; available: boolean }
type Booking = { id: string; slot_id: string; name: string; email: string; status: string; created_at: string }

// No API key here: the page authenticates with the HttpOnly sid cookie the server sets on GET /.
const headers = { 'Content-Type': 'application/json' }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { ...headers, ...init?.headers } })
  const body = await res.json().catch(() => null)
  if (!res.ok || body == null)
    throw Object.assign(new Error(body?.error ?? `Something went wrong (${res.status}). Please try again.`),
      { status: res.status, retryAfter: Number(res.headers.get('Retry-After')) || 0 })
  return body
}

const fmt = (iso: string, opts: Intl.DateTimeFormatOptions) =>
  new Date(iso).toLocaleString('en-GB', { timeZone: 'UTC', ...opts })

const when = (s: Slot) =>
  `${fmt(s.start, { weekday: 'short', day: 'numeric', month: 'short' })}, ` +
  `${fmt(s.start, { hour: '2-digit', minute: '2-digit' })}–${fmt(s.end, { hour: '2-digit', minute: '2-digit' })} UTC`

// Stable identity, so React calls it once on mount rather than on every re-render.
const focusOnMount = (el: HTMLElement | null) => el?.focus()

export default function App() {
  const [slots, setSlots] = useState<Slot[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [selected, setSelected] = useState<Slot | null>(null)
  const [confirmed, setConfirmed] = useState<{ booking: Booking; slot: Slot } | null>(null)
  const [formError, setFormError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const attempt = useRef<{ body: string; key: string } | null>(null)
  // Lives here, not in the form, so switching slots (e.g. after a 409) keeps what the user typed.
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  // Seconds left before the server will take another attempt for this email (from a 429's Retry-After).
  const [wait, setWait] = useState(0)

  useEffect(() => {
    if (wait <= 0) return
    const t = setTimeout(() => setWait(wait - 1), 1000)
    return () => clearTimeout(t)
  }, [wait])

  useEffect(() => setWait(0), [email]) // the limit is per email, so a different email may book at once

  const loadSlots = () =>
    api<{ slots: Slot[] }>('/slots')
      .then((r) => { setSlots(r.slots); setLoadError('') })
      .catch((e: Error) => setLoadError(e.message))

  useEffect(() => { loadSlots() }, [])

  const [mine, setMine] = useState<Booking[] | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [mineEmail, setMineEmail] = useState('')
  const [mineError, setMineError] = useState('')
  const [mineLoading, setMineLoading] = useState(false)
  const [cancelling, setCancelling] = useState<string | null>(null)

  async function loadMine(cursor?: string) {
    // "Load more" pages the email the list was loaded for, not whatever the shared field holds now.
    const who = cursor ? mineEmail : email
    if (!cursor) setMineEmail(who)
    setMineLoading(true)
    setMineError('')
    try {
      const r = await api<{ bookings: Booking[]; next_cursor: string | null }>(
        '/bookings?' + new URLSearchParams({ email: who, ...(cursor && { cursor }) }))
      setMine((prev) => (cursor ? [...(prev ?? []), ...r.bookings] : r.bookings))
      setNextCursor(r.next_cursor)
    } catch (err) {
      setMineError((err as Error).message)
    } finally {
      setMineLoading(false)
    }
  }

  async function cancel(b: Booking) {
    if (!confirm(`Cancel booking ${b.id}?`)) return
    setCancelling(b.id)
    setMineError('')
    const replace = (nb: Booking) => setMine((prev) => prev?.map((x) => (x.id === nb.id ? nb : x)) ?? null)
    try {
      replace(await api<Booking>('/bookings/' + b.id, { method: 'DELETE' }))
      if (confirmed?.booking.id === b.id) setConfirmed(null)
      loadSlots()
    } catch (err) {
      if ((err as { status?: number }).status === 409) replace({ ...b, status: 'cancelled' })
      setMineError((err as Error).message)
    } finally {
      setCancelling(null)
    }
  }

  const slotOf = (id: string) => slots?.find((s) => s.id === id)

  async function book(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selected || submitting || wait > 0) return
    const body = JSON.stringify({ slot_id: selected.id, name, email })
    // Retrying the same body reuses its key, so a lost response can't book twice; an edited body gets a new key.
    if (attempt.current?.body !== body) attempt.current = { body, key: crypto.randomUUID() }
    setSubmitting(true)
    setFormError('')
    try {
      const booking = await api<Booking>('/bookings', {
        method: 'POST',
        body,
        headers: { 'Idempotency-Key': attempt.current.key },
      })
      attempt.current = null
      setConfirmed({ booking, slot: selected })
      setSelected(null)
    } catch (err) {
      // Keep the key only when the outcome is unknown (network error, 5xx, 429); a definitive 4xx would just replay.
      const { status, retryAfter } = err as { status?: number; retryAfter?: number }
      if (status && status >= 400 && status < 500 && status !== 429) attempt.current = null
      if (status === 429) setWait(retryAfter || 0)
      setFormError((err as Error).message)
    } finally {
      setSubmitting(false)
      loadSlots() // the slot may have just been taken, by us or someone else
    }
  }

  return (
    <main className="mx-auto max-w-xl px-4 py-8 text-slate-900">
      <h1 className="text-2xl font-semibold">Book an appointment</h1>

      {confirmed ? (
        <section className="mt-6 rounded-lg border border-emerald-300 bg-emerald-50 p-4" aria-live="polite">
          <h2 ref={focusOnMount} tabIndex={-1} className="text-lg font-semibold text-emerald-900 focus:outline-none">Booking confirmed</h2>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-slate-600">Booking ID</dt>
            <dd className="font-mono">{confirmed.booking.id}</dd>
            <dt className="text-slate-600">Provider</dt>
            <dd>{confirmed.slot.provider}</dd>
            <dt className="text-slate-600">Time</dt>
            <dd>{when(confirmed.slot)}</dd>
          </dl>
          <button
            className="mt-4 min-h-11 rounded-md bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700"
            onClick={() => setConfirmed(null)}
          >
            Book another slot
          </button>
        </section>
      ) : (
        <section className="mt-6">
          <h2 className="text-sm font-medium text-slate-600">Choose a slot</h2>
          {loadError && (
            <p className="mt-3 text-sm text-red-700">
              Couldn't load slots: {loadError}{' '}
              <button className="underline" onClick={loadSlots}>Try again</button>
            </p>
          )}
          {!slots && !loadError && <p className="mt-3 text-sm text-slate-500">Loading slots…</p>}
          {slots?.length === 0 && <p className="mt-3 text-sm text-slate-500">No slots are open right now.</p>}

          <ul className="mt-3 space-y-2">
            {slots?.map((s) => (
              <li key={s.id}>
                <button
                  disabled={!s.available}
                  aria-pressed={selected?.id === s.id}
                  onClick={() => { setSelected(s); setFormError('') }}
                  className="flex min-h-11 w-full items-center justify-between rounded-md border border-slate-300 px-4 py-2 text-left hover:border-slate-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-600 aria-pressed:border-slate-900 aria-pressed:bg-slate-900 aria-pressed:text-white"
                >
                  <span>
                    <span className="block font-medium">{s.provider}</span>
                    <span className="block text-sm">{when(s)}</span>
                  </span>
                  {!s.available && <span className="text-xs font-medium uppercase">Booked</span>}
                </button>

                {selected?.id === s.id && (
                  <form onSubmit={book} className="mt-2 space-y-3 rounded-md border border-slate-200 bg-slate-50 p-4">
                    <label className="block text-sm font-medium">
                      Name
                      <input name="name" required autoComplete="name" autoFocus value={name} onChange={(e) => setName(e.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-normal" />
                    </label>
                    <label className="block text-sm font-medium">
                      Email
                      <input name="email" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-normal" />
                    </label>
                    {formError && <p role="alert" className="text-sm text-red-700">{formError}</p>}
                    {/* aria-disabled, not disabled, while busy: a disabled button would drop keyboard focus to <body>. */}
                    <button
                      disabled={!s.available}
                      aria-disabled={submitting || wait > 0}
                      className="min-h-11 w-full rounded-md bg-slate-900 px-4 py-2 font-medium text-white hover:bg-slate-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:opacity-60 aria-disabled:cursor-not-allowed aria-disabled:opacity-60"
                    >
                      {submitting ? 'Booking…' : wait > 0 ? `Try again in ${wait}s` : 'Book this slot'}
                    </button>
                  </form>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-10 border-t border-slate-200 pt-6">
        <h2 className="text-lg font-semibold">My bookings</h2>
        <form onSubmit={(e) => { e.preventDefault(); loadMine() }} className="mt-3 flex flex-wrap items-end gap-2">
          <label className="block min-w-0 flex-1 text-sm font-medium">
            Email
            <input name="mine-email" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-normal" />
          </label>
          <button
            disabled={mineLoading}
            className="min-h-11 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:opacity-60"
          >
            Show my bookings
          </button>
        </form>

        {mineError && <p role="alert" className="mt-3 text-sm text-red-700">{mineError}</p>}
        {mine?.length === 0 && <p className="mt-3 text-sm text-slate-500">No bookings for this email yet.</p>}

        <ul className="mt-3 space-y-2">
          {mine?.map((b) => {
            const s = slotOf(b.slot_id)
            const active = b.status === 'confirmed'
            return (
              <li key={b.id} className="flex items-center justify-between gap-3 rounded-md border border-slate-300 px-4 py-2">
                <span className="min-w-0">
                  <span className="block font-medium">{s ? s.provider : b.slot_id}</span>
                  {s && <span className="block text-sm">{when(s)}</span>}
                  <span className="block text-xs text-slate-600">
                    {active ? 'Confirmed' : 'Cancelled'} · <span className="font-mono">#{b.id}</span>
                  </span>
                </span>
                {active && (
                  <button
                    onClick={() => cancel(b)}
                    aria-label={`Cancel booking ${b.id}`}
                    disabled={cancelling === b.id}
                    className="min-h-11 shrink-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:border-slate-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:opacity-60"
                  >
                    {cancelling === b.id ? 'Cancelling…' : 'Cancel'}
                  </button>
                )}
              </li>
            )
          })}
        </ul>

        {nextCursor && (
          <button
            onClick={() => loadMine(nextCursor)}
            disabled={mineLoading}
            className="mt-3 min-h-11 w-full rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium hover:border-slate-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:opacity-60"
          >
            {mineLoading ? 'Loading…' : 'Load more'}
          </button>
        )}
      </section>
    </main>
  )
}
