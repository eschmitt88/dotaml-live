import React, { useEffect, useMemo, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { api } from './api.js'

const SAMPLE = [1, 6, 22, 86, 129, 5, 11, 13, 14, 35] // AM/Drow/Zeus/Rubick/Mars vs CM/SF/Puck/Pudge/Sniper
const SLOTS = ['R1', 'R2', 'R3', 'R4', 'R5', 'D1', 'D2', 'D3', 'D4', 'D5']

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

export default function App() {
  const [meta, setMeta] = useState(null)
  const [model, setModel] = useState(null)
  const [draft, setDraft] = useState(SAMPLE)
  const [accountId, setAccountId] = useState('')
  const [mySlot, setMySlot] = useState(0)
  const [comboMode, setComboMode] = useState('synergy')
  const [out, setOut] = useState({})
  const [busy, setBusy] = useState({})
  const [err, setErr] = useState(null)

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setErr(String(e)))
    api.model().then(setModel).catch(() => {})
  }, [])

  const heroName = useMemo(() => {
    const m = {}
    meta?.heroes.forEach((h) => { m[h.id] = h.name })
    return m
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

  if (err && !meta) return <div className="app"><p className="err">Cannot reach API: {err}</p></div>
  if (!meta) return <div className="app"><p>Loading…</p></div>

  const wp = out.winprob
  const curve = out.curve?.curve?.map((p) => ({ minute: p.duration_minutes, win: p.win_prob }))

  return (
    <div className="app">
      <header>
        <h1>dotaml-live <span className="tag">Turbo</span></h1>
        <span className="model">model: {model?.version ?? '?'} · {model?.device ?? '?'}</span>
      </header>

      {err && <p className="err">{err}</p>}

      <section className="draft">
        <h2>Draft</h2>
        <div className="teams">
          <div className="team radiant">
            <h4>Radiant</h4>
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="slot">
                <span>{SLOTS[i]}</span>
                <HeroSelect heroes={meta.heroes} value={draft[i]}
                  onChange={(v) => setDraft((d) => d.map((x, j) => j === i ? v : x))} />
              </div>
            ))}
          </div>
          <div className="team dire">
            <h4>Dire</h4>
            {[5, 6, 7, 8, 9].map((i) => (
              <div key={i} className="slot">
                <span>{SLOTS[i]}</span>
                <HeroSelect heroes={meta.heroes} value={draft[i]}
                  onChange={(v) => setDraft((d) => d.map((x, j) => j === i ? v : x))} />
              </div>
            ))}
          </div>
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
                <YAxis domain={[0.3, 0.8]} tickFormatter={(v) => v.toFixed(2)} />
                <Tooltip formatter={(v) => (v * 100).toFixed(1) + '%'} />
                <ReferenceLine y={0.5} stroke="#888" strokeDasharray="4 4" />
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
              known_radiant: myRad ? known_radiant : known_radiant,
              known_dire, my_side: myRad ? 'radiant' : 'dire',
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

        <Panel title="Top hero combos" busy={busy.combos}
          onRun={() => run('combos', () => api.heroCombos({
            pool: draft.filter(Boolean), size: 2, mode: comboMode, top_k: 12,
          }))}>
          <div className="seg">
            {['synergy', 'kills_per_min'].map((m) => (
              <button key={m} className={comboMode === m ? 'on' : ''} onClick={() => setComboMode(m)}>{m}</button>
            ))}
          </div>
          {out.combos && (
            <ol className="list">
              {out.combos.combos.map((c, i) => (
                <li key={i}>
                  <span>{c.hero_names.join(' + ')}</span>
                  <b>{comboMode === 'synergy'
                    ? (c.score >= 0 ? '+' : '') + (c.score * 100).toFixed(2) + '%'
                    : c.kills_per_min.toFixed(2) + '/min'}</b>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>
    </div>
  )
}
