import { useEffect, useState } from 'react'

const rs = (n) => `Rs.${Number(n).toLocaleString('en-IN')}`

const SECTIONS = [
  { key: 'recover', title: 'Act now: Buy Box lost, recoverable', hint: 'A price drop within margin wins it back.', tone: 'red' },
  { key: 'raise', title: 'Opportunity: raise price', hint: 'We win the Buy Box with room to spare.', tone: 'green' },
  { key: 'below_floor', title: 'Flagged: competitor below our margin floor', hint: 'No price action possible. Watch it or escalate.', tone: 'amber' },
  { key: 'review', title: 'Needs a human', hint: 'Price is not the problem, or the data looks wrong.', tone: 'grey' },
]

async function api(path, method = 'GET') {
  const res = await fetch(path, { method })
  const body = await res.json()
  if (!res.ok) throw new Error(body.detail || 'Request failed')
  return body
}

export default function App() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [toast, setToast] = useState(null)
  const [applying, setApplying] = useState(null)

  const load = async (refresh = false) => {
    setLoading(true)
    setError('')
    try {
      setData(await api(`/api/triage${refresh ? '?refresh=true' : ''}`))
    } catch (e) {
      setError(`Could not reach the backend: ${e.message}`)
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const apply = async (item) => {
    setApplying(item.sku)
    try {
      const s = await api(`/api/skus/${item.sku}/apply`, 'POST')
      setToast(`${s.sku} repriced ${rs(s.old_price)} → ${rs(s.our_price)}. Pushed to marketplace (simulated).`)
      setTimeout(() => setToast(null), 4000)
      await load()
    } catch (e) {
      setError(e.message)
    }
    setApplying(null)
  }

  const reset = async () => { await api('/api/reset', 'POST'); load() }

  const items = data?.items || []
  const by = (bucket) => items.filter((i) => i.status === 'Open' && i.bucket === bucket)
  const repriced = items.filter((i) => i.status === 'Repriced')
  const actionable = by('recover').length + by('raise').length

  return (
    <div className="page">
      <header>
        <div>
          <h1>Pricing Signal</h1>
          <p className="sub">
            {loading ? 'Analysing SKUs…' : `${actionable} decision${actionable === 1 ? '' : 's'} waiting · ${by('below_floor').length} flagged`}
            {data?.generated_at && ` · as of ${new Date(data.generated_at).toLocaleTimeString()}`}
          </p>
        </div>
        <div className="actions">
          <span className={`badge ${data?.llm_enabled ? 'ok' : 'warn'}`}>
            {data?.llm_enabled ? `AI: ${data.model}` : 'AI off: rule-based fallback'}
          </span>
          <button className="ghost" onClick={() => load(true)} disabled={loading}>Re-analyse</button>
          <button className="ghost" onClick={reset}>Reset demo</button>
        </div>
      </header>

      {data?.reasoning && (
        <details className="reasoning">
          <summary title="How the AI worked this out">ⓘ AI reasoning</summary>
          <pre>{data.reasoning}</pre>
        </details>
      )}

      {error && <div className="error">{error}</div>}
      {toast && <div className="toast">✓ {toast}</div>}

      {SECTIONS.map(({ key, title, hint, tone }) => {
        const list = by(key)
        if (!list.length) return null
        return (
          <section key={key}>
            <h2 className={tone}>{title} <span className="count">{list.length}</span></h2>
            <p className="hint">{hint}</p>
            {list.map((i) => <Card key={i.sku} item={i} tone={tone} onApply={apply} busy={applying === i.sku} />)}
          </section>
        )
      })}

      {repriced.length > 0 && (
        <section>
          <h2 className="blue">Repriced today <span className="count">{repriced.length}</span></h2>
          {repriced.map((i) => (
            <div key={i.sku} className="row done">
              <b>{i.sku}</b> {i.brand}: {rs(i.old_price)} → <b>{rs(i.our_price)}</b>
              <span className="muted"> · Buy Box: pending next snapshot · {new Date(i.applied_at).toLocaleTimeString()}</span>
            </div>
          ))}
        </section>
      )}

      {by('hold').length > 0 && (
        <details>
          <summary>No action needed ({by('hold').length})</summary>
          {by('hold').map((i) => (
            <div key={i.sku} className="row">
              <b>{i.sku}</b> {i.brand}: {rs(i.our_price)} vs competitor {rs(i.competitor_price)}.{' '}
              <span className="muted">{i.recommendation?.text || i.reason}</span>
            </div>
          ))}
        </details>
      )}
    </div>
  )
}

function Card({ item, tone, onApply, busy }) {
  const rec = item.recommendation
  const flags = rec?.rule_flags || []
  return (
    <div className={`card ${tone}`}>
      <div className="card-head">
        <div>
          <b>{item.sku}</b> <span className="muted">{item.brand} · changed {item.last_changed}</span>
          {flags.length > 0 && (
            <span className="flag" title={flags.join('\n')}>⚑ For Ranjith's review</span>
          )}
        </div>
        <div className="prices">
          <span>Ours <b>{rs(item.our_price)}</b></span>
          <span>Competitor <b>{rs(item.competitor_price)}</b></span>
          <span>Floor <b>{rs(item.margin_floor)}</b></span>
        </div>
      </div>

      {rec ? (
        <div className="rec">
          <p className="rec-text">{rec.text}</p>
          {flags.length > 0 && <p className="flag-note">AI broke a basic rule: {flags.join(' ')}</p>}
          <div className="rec-foot">
            <span className="muted">
              {rs(item.our_price)} → <b>{rs(rec.price)}</b> ({rec.metrics.change > 0 ? '+' : ''}{rec.metrics.change}) ·{' '}
              {rs(rec.metrics.above_floor)} above floor · {rec.source === 'llm' ? 'AI' : 'Rules'}
              {rec.clamped && ' · AI price was out of bounds and was clamped'}
              {rec.rewritten && !rec.clamped && ' · AI sentence rebuilt from verified numbers'}
              {rec.error && ` · ${rec.error}`}
            </span>
            <button onClick={() => onApply(item)} disabled={busy}>
              {busy ? 'Applying…' : `Apply ${rs(rec.price)}`}
            </button>
          </div>
        </div>
      ) : (
        <p className="reason">{item.reason}</p>
      )}
    </div>
  )
}
