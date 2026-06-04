import React, { useEffect, useMemo, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { api } from './api.js'

const SAMPLE = [1, 6, 22, 86, 129, 5, 11, 13, 14, 35]
const SLOTS = ['R1', 'R2', 'R3', 'R4', 'R5', 'D1', 'D2', 'D3', 'D4', 'D5']
const ATTR_COLOR = { str: '#e0794b', agi: '#16a34a', int: '#3b82f6', all: '#a855f7', '?': '#888' }

function HeroSelect({ heroes, value, onChange }) {
  return (
    <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
      <option value={0}>—</option>
      {heroes.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
    </select>
  )
}

function Panel({ title, children, onRun, busy }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>{title}</h3>
        {onRun && <button disabled={busy} onClick={onRun}>{busy ? '…' : 'Compute'}</button>}
      </div>
      {children}
    </div>
  )
}

// ---------------- Draft analysis tab ----------------

function DraftTab({ meta }) {
  const [draft, setDraft] = useState(SAMPLE)
  const [accountId, setAccountId] = useState('')
  const [mySlot, setMySlot] = useState(0)
  const [out, setOut] = useState({})
  const [busy, setBusy] = useState({})
  const [err, setErr] = useState(null)

  const heroName = useMemo(() => {
    const m = {}; meta.heroes.forEach((h) => { m[h.id] = h.name }); return m
  }, [meta])

  const accounts = () => {
    const a = Array(10).fill(null)
    if (accountId) a[mySlot] = Number(accountId)
    return a
  }
  const run = async (key, fn) => {
    setBusy((b) => ({ ...b, [key]: true })); setErr(null)
    try { const r = await fn(); setOut((o) => ({ ...o, [key]: r })) }
    catch (e) { setErr(String(e)) }
    finally { setBusy((b) => ({ ...b, [key]: false })) }
  }

  const wp = out.winprob
  const curve = out.curve?.curve?.map((p) => ({ minute: p.duration_minutes, win: p.win_prob }))

  return (
    <>
      {err && <p className="err">{err}</p>}
      <section className="draft">
        <h2>Draft</h2>
        <div className="teams">
          {[['radiant', [0, 1, 2, 3, 4]], ['dire', [5, 6, 7, 8, 9]]].map(([side, idxs]) => (
            <div key={side} className={`team ${side}`}>
              <h4>{side[0].toUpperCase() + side.slice(1)}</h4>
              {idxs.map((i) => (
                <div key={i} className="slot">
                  <span>{SLOTS[i]}</span>
                  <HeroSelect heroes={meta.heroes} value={draft[i]}
                    onChange={(v) => setDraft((d) => d.map((x, j) => j === i ? v : x))} />
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="ctrl">
          <label>My slot
            <select value={mySlot} onChange={(e) => setMySlot(Number(e.target.value))}>
              {SLOTS.map((s, i) => <option key={i} value={i}>{s} ({heroName[draft[i]] ?? '—'})</option>)}
            </select>
          </label>
          <label>Account ID (optional)
            <input value={accountId} onChange={(e) => setAccountId(e.target.value)} placeholder="e.g. 3303652" />
          </label>
        </div>
      </section>

      <div className="grid">
        <Panel title="Win probability & duration" busy={busy.winprob}
          onRun={() => run('winprob', () => api.winprob({ heroes: draft, account_ids: accounts() }))}>
          {wp && (
            <div className="big">
              <div><b>{(wp.radiant_win_prob * 100).toFixed(1)}%</b><span>radiant win</span></div>
              <div><b>{wp.predicted_duration_min}</b><span>pred. minutes</span></div>
            </div>
          )}
        </Panel>

        <Panel title="Win vs. duration" busy={busy.curve}
          onRun={() => run('curve', () => api.winVsDuration({ heroes: draft, account_ids: accounts() }))}>
          {curve && (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={curve} margin={{ top: 8, right: 16, bottom: 4, left: -16 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="minute" unit="m" />
                <YAxis domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tickFormatter={(v) => v.toFixed(2)} />
                <Tooltip formatter={(v) => (v * 100).toFixed(1) + '%'} />
                <ReferenceLine y={0.5} stroke="#c9d4e0" strokeWidth={2} strokeDasharray="6 3" />
                <Line type="monotone" dataKey="win" stroke="#16a34a" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Panel>

        <Panel title={`Top hero picks (${SLOTS[mySlot]})`} busy={busy.picks}
          onRun={() => run('picks', () => {
            const myRad = mySlot < 5
            const known_radiant = [0, 1, 2, 3, 4].filter((i) => i !== mySlot).map((i) => draft[i]).filter(Boolean)
            const known_dire = [5, 6, 7, 8, 9].filter((i) => i !== mySlot).map((i) => draft[i]).filter(Boolean)
            return api.heroPicks({
              known_radiant, known_dire, my_side: myRad ? 'radiant' : 'dire',
              account_id: accountId ? Number(accountId) : null, top_k: 10,
            })
          })}>
          {out.picks && (
            <ol className="list">
              {out.picks.picks.map((p) => (
                <li key={p.hero_id}><span>{p.hero_name}</span><b>{(p.mean_winprob * 100).toFixed(1)}%</b></li>
              ))}
            </ol>
          )}
        </Panel>

        <Panel title={`Item build (${SLOTS[mySlot]})`} busy={busy.build}
          onRun={() => run('build', () => api.itemBuild({ heroes: draft, my_slot: mySlot, account_ids: accounts(), t_max: 45 }))}>
          {out.build && (
            <div>
              <div className="inv">{out.build.final_inventory.map((it) => <span key={it.item_id} className="chip">{it.item_name}</span>)}</div>
              <pre className="plan">{out.build.pretty}</pre>
            </div>
          )}
        </Panel>
      </div>
    </>
  )
}

// ---------------- Combo discovery tab ----------------

function AttrTag({ a }) {
  return <span className="attr" style={{ background: ATTR_COLOR[a] || '#888' }}>{a}</span>
}

function DiscoverTab() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [q, setQ] = useState('')
  const [size, setSize] = useState('pairs')      // 'pairs' | 'trios'
  const [sortKey, setSortKey] = useState('fun')
  const [limit, setLimit] = useState(150)

  useEffect(() => { api.combosTable().then(setData).catch((e) => setErr(String(e))) }, [])
  useEffect(() => { setLimit(150) }, [size, q, sortKey])

  const base = data ? (size === 'pairs' ? data.combos : data.trios) || [] : []
  const rows = useMemo(() => {
    if (!base.length) return []
    const syn = base.map((c) => c.synergy), kpm = base.map((c) => c.kpm)
    const sMin = Math.min(...syn), sMax = Math.max(...syn), kMin = Math.min(...kpm), kMax = Math.max(...kpm)
    const nrm = (x, lo, hi) => (hi > lo ? (x - lo) / (hi - lo) : 0)
    return base.map((c) => ({ ...c, fun: nrm(c.synergy, sMin, sMax) + nrm(c.kpm, kMin, kMax) }))
  }, [base])

  const view = useMemo(() => {
    const needle = q.trim().toLowerCase()
    let r = rows
    if (needle) r = r.filter((c) => c.names.some((n) => n.toLowerCase().includes(needle)))
    return [...r].sort((x, y) => y[sortKey] - x[sortKey])
  }, [rows, q, sortKey])

  if (err) return <p className="err">{err}</p>
  if (!data) return <p>Loading combos…</p>
  if (!data.computed) return <p>Combo table not computed yet. Run <code>scripts/precompute_combos.py</code>.</p>

  const Th = ({ k, children }) => (
    <th className={`sortable ${sortKey === k ? 'on' : ''}`} onClick={() => setSortKey(k)}>{children}</th>
  )
  const countLabel = size === 'pairs'
    ? `${data.n_pairs.toLocaleString()} pairs`
    : `top ${data.n_trios_kept.toLocaleString()} of ${data.n_trios_scored.toLocaleString()} trios`

  return (
    <section className="discover">
      <div className="disco-head">
        <div>
          <h2>Hero combo discovery</h2>
          <p className="sub">Draft-independent synergy + action level — find fun {size === 'pairs' ? 'duos' : 'trios'} to
            queue with friends. {countLabel} over {data.n_heroes} heroes.</p>
        </div>
      </div>
      <div className="disco-ctrl">
        <div className="seg">
          {[['pairs', 'Pairs'], ['trios', 'Trios']].map(([k, l]) => (
            <button key={k} className={size === k ? 'on' : ''} onClick={() => setSize(k)}>{l}</button>
          ))}
        </div>
        <input className="search" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="contains hero…  (e.g. 'anti-mage' for its best partners)" />
        <div className="seg">
          {[['fun', 'Most fun'], ['synergy', 'Synergy'], ['kpm', 'Kills/min']].map(([k, label]) => (
            <button key={k} className={sortKey === k ? 'on' : ''} onClick={() => setSortKey(k)}>{label}</button>
          ))}
        </div>
        <span className="count">showing {Math.min(limit, view.length)} of {view.length.toLocaleString()}</span>
      </div>
      <table className="combos">
        <thead>
          <tr>
            <th>#</th><th>Combo</th>
            <Th k="synergy">Synergy</Th><Th k="kpm">Kills/min</Th><Th k="fun">Fun</Th>
          </tr>
        </thead>
        <tbody>
          {view.slice(0, limit).map((c, i) => (
            <tr key={c.ids.join('-')}>
              <td className="rank">{i + 1}</td>
              <td className="combo">
                {c.names.map((n, j) => (
                  <span key={j}>{j > 0 && <span className="plus">+</span>}<AttrTag a={c.attrs[j]} /> {n} </span>
                ))}
              </td>
              <td className={c.synergy >= 0 ? 'pos' : 'neg'}>{c.synergy >= 0 ? '+' : ''}{(c.synergy * 100).toFixed(2)}%</td>
              <td>{c.kpm.toFixed(2)}</td>
              <td><div className="funbar"><div style={{ width: `${(c.fun / 2) * 100}%` }} /></div></td>
            </tr>
          ))}
        </tbody>
      </table>
      {view.length > limit &&
        <button className="more" onClick={() => setLimit((l) => l + 150)}>show more</button>}
    </section>
  )
}

// ---------------- App shell ----------------

export default function App() {
  const [meta, setMeta] = useState(null)
  const [model, setModel] = useState(null)
  const [err, setErr] = useState(null)
  const [tab, setTab] = useState('draft')

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setErr(String(e)))
    api.model().then(setModel).catch(() => {})
  }, [])

  if (err && !meta) return <div className="app"><p className="err">Cannot reach API: {err}</p></div>
  if (!meta) return <div className="app"><p>Loading…</p></div>

  return (
    <div className="app">
      <header>
        <h1>dotaml-live <span className="tag">Turbo</span></h1>
        <nav className="tabs">
          <button className={tab === 'draft' ? 'on' : ''} onClick={() => setTab('draft')}>Draft analysis</button>
          <button className={tab === 'discover' ? 'on' : ''} onClick={() => setTab('discover')}>Combo discovery</button>
        </nav>
        <span className="model">model: {model?.version ?? '?'} · {model?.device ?? '?'}</span>
      </header>
      {tab === 'draft' ? <DraftTab meta={meta} /> : <DiscoverTab />}
    </div>
  )
}
