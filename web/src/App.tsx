import { useEffect, useRef, useState, type FormEvent } from 'react'

type Slot = { id: string; provider: string; start: string; end: string; available: boolean }
type Booking = { id: string; slot_id: string; name: string; email: string; status: string; created_at: string }

// No API key here: the page authenticates with the HttpOnly sid cookie the server sets on GET /.
const headers = { 'Content-Type': 'application/json' }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { ...headers, ...init?.headers } })
  const body = await res.json().catch(() => null)
  if (!res.ok || body == null)
    throw Object.assign(new Error(body?.error ?? `Something went wrong (${res.status}). Please try again.`), { status: res.status })
  return body
}

// All times are shown in UTC, as the API stores them.
const fmt = (iso: string, opts: Intl.DateTimeFormatOptions) =>
  new Date(iso).toLocaleString('en-GB', { timeZone: 'UTC', ...opts })
const hhmm = (iso: string) => fmt(iso, { hour: '2-digit', minute: '2-digit' })
const dayKey = (iso: string) => new Date(iso).toISOString().slice(0, 10)
const longDay = (iso: string) => fmt(iso, { weekday: 'long', day: 'numeric', month: 'long' })
const isMorning = (iso: string) => new Date(iso).getUTCHours() < 12

// Stable identity, so React calls it once on mount rather than on every re-render.
const focusOnMount = (el: HTMLElement | null) => el?.focus()

// Shared control styles. Colours, radii and shadows come from tokens.css.
const field =
  'mt-1.5 block min-h-11 w-full rounded-control border border-line-strong bg-surface px-3 text-base font-normal text-ink-900'
const primary =
  'min-h-12 rounded-control bg-nitrile-600 px-5 text-lg font-bold text-white transition-colors duration-(--duration-quick) hover:bg-nitrile-700 disabled:cursor-not-allowed disabled:opacity-60'
const secondary =
  'min-h-11 rounded-control border border-line-strong bg-surface px-4 font-bold text-ink-900 transition-colors duration-(--duration-quick) hover:border-ink-500 disabled:cursor-not-allowed disabled:opacity-60'

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
  const nameInput = useRef<HTMLInputElement>(null)

  // Filters for the slot picker: '' means any doctor; null day means "the first day with something open".
  const [doctor, setDoctor] = useState('')
  const [day, setDay] = useState<string | null>(null)

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

  const slotOf = (id: string) => slots?.find((s) => s.id === id)

  async function cancel(b: Booking) {
    const s = slotOf(b.slot_id)
    const what = s ? `your ${hhmm(s.start)} appointment on ${longDay(s.start)}` : `booking ${b.id}`
    if (!confirm(`Cancel ${what}?`)) return
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

  function pick(s: Slot) {
    setSelected(s)
    setConfirmed(null)
    setFormError('')
    // Focusing the name field also scrolls the form into view on a phone, where it sits below the times.
    requestAnimationFrame(() => nameInput.current?.focus())
  }

  async function book(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!selected) return
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
      const status = (err as { status?: number }).status
      if (status && status >= 400 && status < 500 && status !== 429) attempt.current = null
      setFormError((err as Error).message)
    } finally {
      setSubmitting(false)
      loadSlots() // the slot may have just been taken, by us or someone else
    }
  }

  // --- Slot picker: doctor filter → day strip → that day's times, split into morning and afternoon.
  const doctors = [...new Set(slots?.map((s) => s.provider))].sort()
  const shown = (slots ?? []).filter((s) => !doctor || s.provider === doctor)
  const days = [...new Set(slots?.map((s) => dayKey(s.start)))].sort().map((key) => {
    const first = slots!.find((s) => dayKey(s.start) === key)!
    return {
      key,
      label: longDay(first.start),
      dow: fmt(first.start, { weekday: 'short' }),
      dom: fmt(first.start, { day: 'numeric' }),
      open: shown.filter((s) => s.available && dayKey(s.start) === key).length,
    }
  })
  const activeDay =
    days.find((d) => d.key === day && d.open > 0) ?? days.find((d) => d.open > 0) ?? days[0]
  const today = shown
    .filter((s) => activeDay && dayKey(s.start) === activeDay.key)
    .sort((a, b) => a.start.localeCompare(b.start) || a.provider.localeCompare(b.provider))
  const parts = [
    { label: 'Morning', slots: today.filter((s) => isMorning(s.start)) },
    { label: 'Afternoon', slots: today.filter((s) => !isMorning(s.start)) },
  ].filter((p) => p.slots.length)
  const todayOpen = today.filter((s) => s.available).length

  // The selected slot as it is now, so the form knows if someone else just took it.
  const current = selected && (slotOf(selected.id) ?? selected)

  return (
    <div className="min-h-dvh bg-paper text-ink-900">
      <div className="mx-auto max-w-page px-4 pt-6 pb-16 sm:px-10 sm:pt-12">
        <header className="flex items-center gap-2.5">
          <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
            <rect width="28" height="28" rx="7" className="fill-nitrile-600" />
            <path d="M14 7v14M7 14h14" stroke="#fff" strokeWidth="3.2" strokeLinecap="round" />
          </svg>
          <span className="text-lg font-bold">Clinic booking</span>
        </header>

        <div className="mt-10 max-w-prose">
          <h1 className="text-[1.75rem] leading-tight font-black tracking-tight sm:text-display">Book an appointment</h1>
          <p className="mt-2.5 text-lg text-ink-500">
            Pick a time, add your name and email, and you’re booked. All times are UTC.
          </p>
        </div>

        <div className="mt-9 flex flex-wrap items-start gap-8">
          {/* Times */}
          <section aria-labelledby="choose-day" className="min-w-0 flex-[999_1_560px]">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <h2 id="choose-day" className="text-xl font-bold">Choose a day</h2>
              {doctors.length > 1 && (
                <label className="flex items-center gap-2.5 text-sm font-medium">
                  Doctor
                  <select
                    value={doctor}
                    onChange={(e) => setDoctor(e.target.value)}
                    className="min-h-11 min-w-44 cursor-pointer rounded-control border border-line-strong bg-surface px-3 text-base text-ink-900"
                  >
                    <option value="">Any doctor</option>
                    {doctors.map((d) => <option key={d} value={d}>{d}</option>)}
                  </select>
                </label>
              )}
            </div>

            {loadError && (
              <p role="alert" className="mt-4 rounded-control bg-danger-100 px-4 py-3 text-danger-600">
                Couldn’t load open times: {loadError}{' '}
                <button className="font-bold underline" onClick={loadSlots}>Try again</button>
              </p>
            )}
            {!slots && !loadError && (
              <div aria-hidden="true" className="mt-4 flex gap-2">
                {Array.from({ length: 6 }, (_, i) => (
                  <div key={i} className="h-21 w-19 shrink-0 animate-pulse rounded-control bg-taken" />
                ))}
              </div>
            )}
            {!slots && !loadError && <p className="sr-only" role="status">Loading open times…</p>}
            {slots?.length === 0 && (
              <div className="mt-4 rounded-panel border-2 border-dashed border-line-strong p-5">
                <p className="font-bold">No times are open right now</p>
                <p className="mt-1 text-ink-500">New times are added regularly. Check back soon.</p>
              </div>
            )}

            {days.length > 0 && (
              <div role="group" aria-label="Days" className="-mx-0.5 mt-3.5 flex gap-2 overflow-x-auto px-0.5 pt-0.5 pb-2">
                {days.map((d) => {
                  const on = d.key === activeDay?.key
                  return (
                    <button
                      key={d.key}
                      disabled={d.open === 0}
                      aria-pressed={on}
                      aria-label={`${d.label}, ${d.open || 'none'} open`}
                      onClick={() => setDay(d.key)}
                      className={
                        'min-h-21 w-19 shrink-0 rounded-control border px-1.5 py-2.5 text-center transition-colors duration-(--duration-quick) ' +
                        (on
                          ? 'border-ink-900 bg-ink-900 text-white'
                          : d.open
                            ? 'border-line-strong bg-surface hover:border-ink-500'
                            : 'cursor-not-allowed border-dashed border-line-strong text-ink-500')
                      }
                    >
                      <span className="block text-[0.8125rem]">{d.dow}</span>
                      <span className="block text-2xl leading-tight font-black">{d.dom}</span>
                      <span className={'mt-1 block text-xs ' + (on ? 'text-nitrile-100' : d.open ? 'font-bold text-nitrile-700' : 'text-ink-500')}>
                        {d.open ? `${d.open} open` : 'None'}
                      </span>
                    </button>
                  )
                })}
              </div>
            )}

            {activeDay && (
              <>
                <div className="mt-5 flex items-baseline justify-between gap-3 border-b border-line pb-2.5">
                  <h3 className="text-lg font-bold">{activeDay.label}</h3>
                  <span className="text-sm text-ink-500">
                    {todayOpen === 1 ? '1 open time' : `${todayOpen} open times`}
                  </span>
                </div>

                {parts.map((p) => (
                  <div key={p.label} className="mt-4.5">
                    <p className="mb-2.5 text-sm font-bold text-ink-700">{p.label}</p>
                    <ul className="grid grid-cols-[repeat(auto-fill,minmax(7.5rem,1fr))] gap-2.5">
                      {p.slots.map((s) => {
                        const on = selected?.id === s.id
                        return (
                          <li key={s.id}>
                            <button
                              disabled={!s.available}
                              aria-pressed={on}
                              aria-label={`${hhmm(s.start)} with ${s.provider}${s.available ? '' : ', booked'}`}
                              onClick={() => pick(s)}
                              className={
                                'min-h-16 w-full rounded-control px-3 py-2.5 text-left transition-colors duration-(--duration-quick) ' +
                                (!s.available
                                  ? 'cursor-not-allowed border border-taken bg-taken text-ink-500'
                                  : on
                                    ? 'border-2 border-card-500 bg-card-300 px-[11px] py-[9px] text-card-ink'
                                    : 'border border-line-strong bg-surface hover:border-nitrile-600')
                              }
                            >
                              <span className={'block text-[1.1875rem] leading-tight font-black ' + (s.available ? '' : 'line-through decoration-ink-300')}>
                                {hhmm(s.start)}
                              </span>
                              <span className={'mt-0.5 block text-[0.8125rem] ' + (on ? '' : 'text-ink-500')}>
                                {s.available ? s.provider : 'Booked'}
                              </span>
                            </button>
                          </li>
                        )
                      })}
                    </ul>
                  </div>
                ))}

                {today.length === 0 && (
                  <div className="mt-4.5 rounded-panel border-2 border-dashed border-line-strong p-5">
                    <p className="font-bold">{doctor || 'No doctor'} has no times on {activeDay.label.split(' ')[0]}.</p>
                    <p className="mt-1 text-ink-500">Pick another day, or choose Any doctor.</p>
                  </div>
                )}
              </>
            )}
          </section>

          {/* Your appointment */}
          <aside aria-label="Your appointment" className="w-full min-w-0 flex-[1_1_320px]">
            {confirmed ? (
              <div aria-live="polite">
                <h2
                  ref={focusOnMount}
                  tabIndex={-1}
                  className="mb-3.5 flex items-center gap-2 font-bold text-ok-600 focus:outline-none"
                >
                  <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
                    <circle cx="10" cy="10" r="9" className="fill-ok-100" />
                    <path d="M6 10.5l2.6 2.6L14 7.6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  You’re booked
                </h2>
                <div className="relative animate-card-in rounded-card bg-card-300 px-6 pt-6 pb-5.5 text-card-ink shadow-card">
                  <span aria-hidden="true" className="absolute top-5 right-5 size-3.5 rounded-full bg-paper shadow-[inset_0_1px_2px_rgb(74_59_12/0.35)]" />
                  <p className="text-sm font-bold">Your appointment</p>
                  <p className="mt-4.5 text-lg font-medium">{longDay(confirmed.slot.start)}</p>
                  <p className="mt-0.5 text-time font-black">{hhmm(confirmed.slot.start)}</p>
                  <p className="mt-1.5 text-sm">until {hhmm(confirmed.slot.end)} UTC</p>
                  <p className="mt-4 text-[1.1875rem] font-bold">{confirmed.slot.provider}</p>
                  <div className="mt-5 flex items-baseline justify-between gap-3 border-t-2 border-dashed border-card-500 pt-4">
                    <span className="text-sm">Booking reference</span>
                    <span className="font-mono text-[1.75rem] font-medium">{confirmed.booking.id}</span>
                  </div>
                </div>
                <button className={secondary + ' mt-5 min-h-12 w-full'} onClick={() => setConfirmed(null)}>
                  Book another time
                </button>
              </div>
            ) : current ? (
              <form onSubmit={book} className="flex flex-col gap-4 rounded-panel bg-surface p-6 shadow-panel">
                <div className="rounded-control bg-card-100 px-4 py-3.5 text-card-ink">
                  <p className="text-sm">{longDay(current.start)}</p>
                  <p className="mt-0.5 text-2xl font-black">{hhmm(current.start)}–{hhmm(current.end)}</p>
                  <p className="mt-0.5 font-bold">{current.provider}</p>
                </div>
                <label className="block text-sm font-medium">
                  Name
                  <input ref={nameInput} name="name" required autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} className={field} />
                </label>
                <label className="block text-sm font-medium">
                  Email
                  <input name="email" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={field} />
                </label>
                {!current.available && !formError && (
                  <p role="alert" className="rounded-control bg-danger-100 px-3 py-2 text-sm text-danger-600">
                    Someone just booked this time. Pick another one.
                  </p>
                )}
                {formError && <p role="alert" className="rounded-control bg-danger-100 px-3 py-2 text-sm text-danger-600">{formError}</p>}
                <button disabled={submitting || !current.available} className={primary}>
                  {submitting ? 'Booking…' : `Book ${hhmm(current.start)}`}
                </button>
              </form>
            ) : (
              <div className="rounded-panel border-2 border-dashed border-line-strong px-6 py-7">
                <p className="text-lg font-bold">No time picked yet</p>
                <p className="mt-1.5 text-[0.9375rem] text-ink-500">
                  Choose an open time and your booking details will appear here.
                </p>
              </div>
            )}
          </aside>
        </div>

        {/* My bookings */}
        <section aria-labelledby="mine" className="mt-16 max-w-176 border-t border-line pt-8">
          <h2 id="mine" className="text-2xl font-black">My bookings</h2>
          <p className="mt-1.5 text-[0.9375rem] text-ink-500">See or cancel what you’ve booked with this email.</p>
          <form onSubmit={(e) => { e.preventDefault(); loadMine() }} className="mt-4 flex flex-wrap items-end gap-2.5">
            <label className="block min-w-0 flex-[1_1_240px] text-sm font-medium">
              Email
              <input name="mine-email" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={field} />
            </label>
            <button disabled={mineLoading} className={secondary}>
              {mineLoading && !mine ? 'Loading…' : 'Show my bookings'}
            </button>
          </form>

          {mineError && <p role="alert" className="mt-3 rounded-control bg-danger-100 px-3 py-2 text-sm text-danger-600">{mineError}</p>}
          {mine?.length === 0 && (
            <p className="mt-4 text-ink-500">No bookings for {mineEmail} yet. Pick a time above to make one.</p>
          )}

          <ul className="mt-5 flex flex-col gap-2.5">
            {mine?.map((b) => {
              const s = slotOf(b.slot_id)
              const active = b.status === 'confirmed'
              return (
                <li
                  key={b.id}
                  className={
                    'flex items-center gap-4 rounded-panel px-4 py-3.5 ' +
                    (active ? 'bg-surface shadow-[0_1px_0_var(--color-line)]' : 'bg-paper ring-1 ring-line ring-inset')
                  }
                >
                  <div className={'w-16 flex-none rounded-control py-1.5 text-center ' + (active ? 'bg-nitrile-100 text-nitrile-900' : 'bg-taken text-ink-500')}>
                    {s ? (
                      <>
                        <span className="block text-xs">{fmt(s.start, { weekday: 'short' })}</span>
                        <span className="block text-[1.375rem] leading-tight font-black">{fmt(s.start, { day: 'numeric' })}</span>
                      </>
                    ) : (
                      <span className="block py-2 text-xs">Slot {b.slot_id}</span>
                    )}
                  </div>
                  <div className="min-w-0 flex-auto">
                    <p className={'font-bold ' + (active ? '' : 'text-ink-500 line-through decoration-ink-300')}>
                      {s ? `${hhmm(s.start)} with ${s.provider}` : `Slot ${b.slot_id}`}
                    </p>
                    <p className="mt-0.5 text-[0.8125rem] text-ink-500">
                      {active ? 'Confirmed' : 'Cancelled'}, reference <span className="font-mono">{b.id}</span>
                    </p>
                  </div>
                  {active && (
                    <button
                      onClick={() => cancel(b)}
                      aria-label={`Cancel booking ${b.id}`}
                      disabled={cancelling === b.id}
                      className="min-h-11 flex-none rounded-control px-3.5 font-bold text-danger-600 transition-colors duration-(--duration-quick) hover:bg-danger-100 disabled:opacity-60"
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
              className="mt-3 min-h-11 w-full rounded-control border border-dashed border-line-strong font-medium text-ink-700 hover:border-ink-500 disabled:opacity-60"
            >
              {mineLoading ? 'Loading…' : 'Load more'}
            </button>
          )}
        </section>
      </div>
    </div>
  )
}
