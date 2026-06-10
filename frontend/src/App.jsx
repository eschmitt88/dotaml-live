import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { api } from './api.js'

const SAMPLE = [1, 6, 22, 86, 129, 5, 11, 13, 14, 35]
const ATTR_COLOR = { str: '#e0794b', agi: '#16a34a', int: '#3b82f6', all: '#a855f7', '?': '#888' }
const RAD = [0, 1, 2, 3, 4]
const DIRE = [5, 6, 7, 8, 9]
const PCOLOR = ['#3b82f6', '#a855f7', '#ec4899', '#f59e0b']
const DEFAULT_PLAYERS = [
  { id: 'p1', name: 'Alaric', account: '' },
  { id: 'p2', name: 'wuts a dota', account: '' },
]

// ---- localStorage-backed state ----
function usePersist(key, initial) {
  const [v, setV] = useState(() => {
    try { const s = localStorage.getItem(key); return s ? JSON.parse(s) : initial } catch { return initial }
  })
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify(v)) } catch {} }, [key, v])
  return [v, setV]
}

// ---- searchable, alphabetical, clearable hero picker ----
function HeroCombo({ heroes, value, onChange, placeholder = '— empty —', nHeroes, onTabCommit }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hi, setHi] = useState(0)
  const wrap = useRef(null)
  const cur = heroes.find((h) => h.id === value)
  const unsupported = (id) => nHeroes != null && id >= nHeroes

  const matches = useMemo(() => {
    const n = q.trim().toLowerCase()
    const arr = n ? heroes.filter((h) => h.name.toLowerCase().includes(n)) : heroes
    return arr.slice(0, 60)
  }, [heroes, q])
  useEffect(() => { setHi(0) }, [q, open])

  const pick = (h) => { onChange(h ? h.id : 0); setOpen(false); setQ('') }
  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((i) => Math.min(i + 1, matches.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (matches[hi]) pick(matches[hi]) }
    else if (e.key === 'Tab') {
      // commit the highlighted hero instead of cancelling, when a query is typed
      if (q.trim() && matches[hi]) { e.preventDefault(); pick(matches[hi]); onTabCommit && onTabCommit() }
      else { setOpen(false); setQ('') }
    }
    else if (e.key === 'Escape') { setOpen(false); setQ('') }
  }

  return (
    <div className={`combo ${open ? 'open' : ''} ${cur ? 'filled' : ''}`} ref={wrap}
      onBlur={(e) => { if (!wrap.current.contains(e.relatedTarget)) { setOpen(false); setQ('') } }}>
      {open ? (
        <input autoFocus className="combo-input" value={q} placeholder={cur ? cur.name : 'type hero…'}
          onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} />
      ) : (
        <button className={`combo-btn ${cur && unsupported(cur.id) ? 'unsupported' : ''}`} onClick={() => setOpen(true)}>
          {cur
            ? <><i className="dot" style={{ background: ATTR_COLOR[cur.attr] }} />{cur.name}
                {unsupported(cur.id) && <span className="warn" title="not in the model — treated as an unknown hero">⚠</span>}</>
            : <span className="ph">{placeholder}</span>}
        </button>
      )}
      {cur && !open && (
        <button className="combo-x" tabIndex={-1} title="clear"
          onMouseDown={(e) => { e.preventDefault(); onChange(0) }}>×</button>
      )}
      {open && (
        <ul className="combo-list">
          {matches.map((h, i) => (
            <li key={h.id} className={`${i === hi ? 'hi' : ''} ${unsupported(h.id) ? 'unsupported' : ''}`}
              onMouseEnter={() => setHi(i)}
              onMouseDown={(e) => { e.preventDefault(); pick(h) }}>
              <i className="dot" style={{ background: ATTR_COLOR[h.attr] }} />{h.name}
              {unsupported(h.id) && <span className="warn" title="not in the model — treated as an unknown hero">not in model</span>}
            </li>
          ))}
          {!matches.length && <li className="none">no match</li>}
        </ul>
      )}
    </div>
  )
}

// ---- slot player assignment via popover ----
function PlayerPicker({ players, value, onPick }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef(null)
  const selIdx = players.findIndex((p) => p.id === value)
  const sel = players[selIdx]
  return (
    <div className="ppick" ref={wrap}
      onBlur={(e) => { if (!wrap.current.contains(e.relatedTarget)) setOpen(false) }}>
      <button className={`pbadge ${sel ? 'set' : ''}`} title={sel ? sel.name : 'assign player'}
        onClick={() => setOpen((o) => !o)}
        style={sel ? { background: PCOLOR[selIdx], borderColor: PCOLOR[selIdx], color: '#fff' } : {}}>
        {sel ? sel.name[0].toUpperCase() : '+'}
      </button>
      {open && (
        <div className="ppop">
          {players.map((p, i) => (
            <button key={p.id} className={`popt ${value === p.id ? 'on' : ''}`}
              onMouseDown={(e) => { e.preventDefault(); onPick(value === p.id ? null : p.id); setOpen(false) }}>
              <i className="pdot" style={{ background: PCOLOR[i] }} />
              <span>{p.name}</span>
              {value === p.id && <span className="pcheck">✓</span>}
            </button>
          ))}
          <button className="popt clear" onMouseDown={(e) => { e.preventDefault(); onPick(null); setOpen(false) }}>
            <i className="pdot none" /><span>none</span>
          </button>
        </div>
      )}
    </div>
  )
}

// ---- screenshot → draft (paste / drop / pick a Dota screenshot) ----
// After a fill, the shot sits in the server's labeling queue; fixing the
// slots and hitting ✓ stores the corrected board as its ground truth.
function ScreenshotFill({ onDraft, draft, pending, setPending }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [drag, setDrag] = useState(false)
  const fileRef = useRef(null)
  const busyRef = useRef(false)

  const handle = async (blob) => {
    if (!blob || !blob.type.startsWith('image/') || busyRef.current) return
    busyRef.current = true
    setBusy(true); setMsg(null); setPending(null)
    try {
      const r = await api.draftFromScreenshot(blob)
      const n = r.detections.length
      onDraft([...r.radiant, ...r.dire])
      if (r.shot_id && !r.already_labeled) setPending(r.shot_id)
      setMsg(n
        ? { ok: true, text: `found ${n}/10 heroes in ${(r.elapsed_ms / 1000).toFixed(1)}s` }
        : { ok: false, text: 'no heroes found — include the top bar in the shot' })
    } catch (e) { setMsg({ ok: false, text: String(e) }) }
    busyRef.current = false
    setBusy(false)
  }

  const confirm = async () => {
    try {
      await api.labelScreenshot(pending, {
        radiant: draft.slice(0, 5), dire: draft.slice(5), labeled_by: 'human' })
      setMsg({ ok: true, text: 'ground truth saved ✓' })
      setPending(null)
    } catch (e) { setMsg({ ok: false, text: String(e) }) }
  }

  useEffect(() => {
    const onPaste = (e) => {
      if (/INPUT|TEXTAREA/.test(e.target?.tagName || '')) return
      const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'))
      if (item) { e.preventDefault(); handle(item.getAsFile()) }
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className={`shot ${drag ? 'drag' : ''} ${busy ? 'busy' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); handle(e.dataTransfer.files?.[0]) }}
      onClick={() => !busy && fileRef.current?.click()}>
      <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }}
        onChange={(e) => { handle(e.target.files?.[0]); e.target.value = '' }} />
      <span className="shot-main">
        {busy ? '⏳ reading screenshot…' : <>📷 <b>Screenshot → draft</b></>}
      </span>
      {!busy && !pending && <span className="shot-hint">paste (Ctrl+V), drop, or click</span>}
      {msg && <span className={`shot-msg ${msg.ok ? 'ok' : 'bad'}`}>{msg.text}</span>}
      {pending && !busy && (
        <span className="shot-confirm" onClick={(e) => e.stopPropagation()}>
          <span className="shot-hint">fix any wrong slots, then</span>
          <button className="shot-ok" onClick={confirm}>✓ confirm ground truth</button>
          <button className="shot-skip" title="leave in the labeling queue"
            onClick={() => setPending(null)}>later</button>
        </span>
      )}
    </div>
  )
}

function Card({ title, sub, right, children, className = '' }) {
  return (
    <div className={`card ${className}`}>
      <div className="card-head">
        <div><h3>{title}</h3>{sub && <span className="card-sub">{sub}</span>}</div>
        {right}
      </div>
      {children}
    </div>
  )
}

// ---------------- Draft analysis tab ----------------

function DraftTab({ meta, draft, setDraft, nHeroes, pendingShot, setPendingShot }) {
  const heroes = useMemo(
    () => [...meta.heroes].sort((a, b) => a.name.localeCompare(b.name)), [meta])
  const heroById = useMemo(() => {
    const m = {}; meta.heroes.forEach((h) => { m[h.id] = h }); return m
  }, [meta])

  const [mySide, setMySide] = usePersist('dl.side', 'radiant')
  const [focusSlot, setFocusSlot] = useState(0)
  const [slotPlayer, setSlotPlayer] = useState(Array(10).fill(null))
  const [players, setPlayers] = usePersist('dl.players', DEFAULT_PLAYERS)
  const [favorites, setFavorites] = usePersist('dl.favs', [])
  const [auto, setAuto] = usePersist('dl.auto', true)
  const [settings, setSettings] = useState(false)

  const [out, setOut] = useState({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const seq = useRef(0)

  const setHero = (i, v) => setDraft((d) => d.map((x, j) => (j === i ? v : x)))
  const chooseSide = (s) => { setMySide(s); setFocusSlot(s === 'radiant' ? 0 : 5) }
  const clearAll = () => {
    setDraft(Array(10).fill(0))
    setSlotPlayer(Array(10).fill(null))
    setFocusSlot(mySide === 'radiant' ? 0 : 5)
  }
  const swapSides = () => {
    // move heroes (and their assigned players) between Radiant and Dire
    setDraft((d) => [...d.slice(5, 10), ...d.slice(0, 5)])
    setSlotPlayer((sp) => [...sp.slice(5, 10), ...sp.slice(0, 5)])
  }

  const accounts = () => {
    const a = Array(10).fill(null)
    slotPlayer.forEach((pid, i) => {
      const p = players.find((x) => x.id === pid)
      if (p && p.account) a[i] = Number(p.account)
    })
    return a
  }
  const focusPlayer = () => players.find((p) => p.id === slotPlayer[focusSlot])
  const picksReq = () => ({
    known_radiant: RAD.filter((i) => i !== focusSlot).map((i) => draft[i]).filter(Boolean),
    known_dire: DIRE.filter((i) => i !== focusSlot).map((i) => draft[i]).filter(Boolean),
    my_side: focusSlot < 5 ? 'radiant' : 'dire',
    account_id: focusPlayer()?.account ? Number(focusPlayer().account) : null,
    top_k: 10,
  })

  async function computeAll() {
    const my = ++seq.current
    setErr(null); setBusy(true)
    const accs = accounts()
    const pr = picksReq()
    const jobs = [
      ['winprob', () => api.winprob({ heroes: draft, account_ids: accs })],
      ['curve', () => api.winVsDuration({ heroes: draft, account_ids: accs })],
      ['picks', () => api.heroPicks(pr)],
    ]
    if (draft[focusSlot]) jobs.push(['build', () => api.itemBuild({ heroes: draft, my_slot: focusSlot, account_ids: accs, t_max: 45 })])
    else setOut((o) => ({ ...o, build: null }))
    if (favorites.length) jobs.push(['favs', () => api.heroPicks({ ...pr, candidate_heroes: favorites, top_k: favorites.length })])
    else setOut((o) => ({ ...o, favs: null }))

    await Promise.all(jobs.map(async ([k, fn]) => {
      try { const r = await fn(); if (seq.current === my) setOut((o) => ({ ...o, [k]: r })) }
      catch (e) { if (seq.current === my) setErr(String(e)) }
    }))
    if (seq.current === my) setBusy(false)
  }

  // auto-recompute (debounced) whenever the board changes
  useEffect(() => {
    if (!auto) return
    const t = setTimeout(computeAll, 350)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(draft), focusSlot, JSON.stringify(slotPlayer), JSON.stringify(players),
      JSON.stringify(favorites), auto])

  const assignPlayer = (i, pid) => setSlotPlayer((sp) =>
    sp.map((x, j) => (j === i ? pid : (pid && x === pid ? null : x))))  // a player owns one slot

  const toggleFav = (id) => setFavorites((f) => (f.includes(id) ? f.filter((x) => x !== id) : [...f, id]))

  const wp = out.winprob
  const curve = out.curve?.curve?.map((p) => ({ minute: p.duration_minutes, win: p.win_prob }))
  const favWin = useMemo(() => {
    const m = {}; (out.favs?.picks || []).forEach((p) => { m[p.hero_id] = p.mean_winprob }); return m
  }, [out.favs])

  const sideOrder = mySide === 'radiant' ? ['radiant', 'dire'] : ['dire', 'radiant']
  const slotName = (i) => (draft[i] ? heroById[draft[i]]?.name : '—')

  // after a Tab-commit, move to the next slot's combo in visual order (stay on the last one)
  const tabToNext = (i) => {
    const order = sideOrder.flatMap((s) => (s === 'radiant' ? RAD : DIRE))
    const next = order[order.indexOf(i) + 1]
    setTimeout(() => {  // wait for the picked combo to close and re-render
      const btn = document.querySelector(`.teams .slot[data-slot="${next ?? i}"] .combo-btn`)
      if (!btn) return
      if (next != null) btn.click()  // opens the combo — its input autofocuses
      else btn.focus()
    }, 0)
  }

  const Team = ({ side }) => {
    const idxs = side === 'radiant' ? RAD : DIRE
    const mine = side === mySide
    return (
      <div className={`team ${side} ${mine ? 'mine' : ''}`}>
        <div className="team-head">
          <span className="team-name">{side}</span>
          {mine && <span className="you">you</span>}
        </div>
        {idxs.map((i) => (
          <div key={i} data-slot={i} className={`slot ${focusSlot === i ? 'focus' : ''}`}>
            <button className="rec-dot" title="recommend for this slot"
              onClick={() => setFocusSlot(i)}>{focusSlot === i ? '◉' : '○'}</button>
            <HeroCombo heroes={heroes} value={draft[i]} onChange={(v) => setHero(i, v)} nHeroes={nHeroes}
              onTabCommit={() => tabToNext(i)} />
            {draft[i] !== 0 && (
              <button className="fav-star" title="favorite"
                onClick={() => toggleFav(draft[i])}>{favorites.includes(draft[i]) ? '★' : '☆'}</button>
            )}
            <PlayerPicker players={players} value={slotPlayer[i]} onPick={(pid) => assignPlayer(i, pid)} />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="board">
      {/* ----- LEFT: draft input ----- */}
      <aside className="left">
        <div className="sidebar-row">
          <div className="side-pick">
            <span className="lbl">I'm on</span>
            <div className="seg big">
              {['radiant', 'dire'].map((s) => (
                <button key={s} className={`${mySide === s ? 'on' : ''} ${s}`} onClick={() => chooseSide(s)}>
                  {s[0].toUpperCase() + s.slice(1)}
                </button>
              ))}
            </div>
          </div>
          <button className="clear-btn" title="clear all heroes & players" onClick={clearAll}>Clear</button>
          <button className="gear" title="players & settings" onClick={() => setSettings((s) => !s)}>⚙</button>
        </div>

        {settings && (
          <div className="settings">
            <p className="hint">Set each account ID once — used for personalized predictions.</p>
            {players.map((p, i) => (
              <div key={p.id} className="prow">
                <i className="dot" style={{ background: PCOLOR[i] }} />
                <input className="pname" value={p.name}
                  onChange={(e) => setPlayers((ps) => ps.map((x) => x.id === p.id ? { ...x, name: e.target.value } : x))} />
                <input className="pacct" value={p.account} placeholder="account ID"
                  onChange={(e) => setPlayers((ps) => ps.map((x) => x.id === p.id ? { ...x, account: e.target.value.replace(/\D/g, '') } : x))} />
              </div>
            ))}
            <label className="autorow">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
              auto-recompute as I draft
            </label>
          </div>
        )}

        <ScreenshotFill onDraft={(d) => setDraft(d)} draft={draft}
          pending={pendingShot} setPending={setPendingShot} />

        <div className="teams">
          {/* render Team as a plain call: an inline component type would remount (and close an
              open combo, dropping focus mid-typing) on every auto-recompute re-render */}
          {Team({ side: sideOrder[0] })}
          <button className="swap-btn" title="swap heroes between Radiant and Dire" onClick={swapSides}>
            ⇅ swap sides
          </button>
          {Team({ side: sideOrder[1] })}
        </div>

        <div className="favs">
          <div className="favs-head">
            <span>Favorites <small>win% for {focusSlot < 5 ? 'R' : 'D'}{(focusSlot % 5) + 1}</small></span>
            <div className="fav-add">
              <HeroCombo heroes={heroes.filter((h) => !favorites.includes(h.id))} value={0}
                placeholder="＋ add" onChange={(v) => v && toggleFav(v)} nHeroes={nHeroes} />
            </div>
          </div>
          {favorites.length === 0 && <p className="muted">Star a hero, or add one, to track its win rate.</p>}
          <div className="fav-list">
            {favorites.map((id) => {
              const h = heroById[id]; if (!h) return null
              const w = favWin[id]
              return (
                <button key={id} className="fav-chip" title="pick for focused slot"
                  onClick={() => setHero(focusSlot, id)}>
                  <i className="dot" style={{ background: ATTR_COLOR[h.attr] }} />
                  <span className="fc-name">{h.name}</span>
                  <b className={w != null ? (w >= 0.5 ? 'pos' : 'neg') : ''}>
                    {w != null ? `${(w * 100).toFixed(1)}%` : '—'}</b>
                  <span className="fc-x" onClick={(e) => { e.stopPropagation(); toggleFav(id) }}>×</span>
                </button>
              )
            })}
          </div>
        </div>
      </aside>

      {/* ----- RIGHT: predictions ----- */}
      <main className="right">
        <div className="pred-head">
          <span className="status">{busy ? 'computing…' : (auto ? 'live' : 'manual')}</span>
          {!auto && <button onClick={computeAll} disabled={busy}>Recompute</button>}
          {err && <span className="err inline">{err}</span>}
        </div>

        <Card className="picks" title="Top hero picks"
          sub={`for ${focusSlot < 5 ? 'Radiant' : 'Dire'} slot ${(focusSlot % 5) + 1}${slotName(focusSlot) !== '—' ? ` · currently ${slotName(focusSlot)}` : ''}`}>
          {out.picks ? (
            <ol className="picklist">
              {out.picks.picks.map((p, i) => {
                const h = heroById[p.hero_id]
                return (
                  <li key={p.hero_id} onClick={() => setHero(focusSlot, p.hero_id)} title="assign to your slot">
                    <span className="prank">{i + 1}</span>
                    <i className="dot" style={{ background: ATTR_COLOR[h?.attr || '?'] }} />
                    <span className="pname">{p.hero_name}</span>
                    <div className="pbar"><div style={{ width: `${p.mean_winprob * 100}%` }} /></div>
                    <b>{(p.mean_winprob * 100).toFixed(1)}%</b>
                  </li>
                )
              })}
            </ol>
          ) : <p className="muted">Pick a slot (○) to get recommendations.</p>}
        </Card>

        <div className="pred-row">
          <Card className="winprob" title="Win probability">
            {wp ? (
              <div className="wp">
                <div className="wp-bar">
                  <div className="rad" style={{ width: `${wp.radiant_win_prob * 100}%` }} />
                  <div className="dire" style={{ width: `${(1 - wp.radiant_win_prob) * 100}%` }} />
                </div>
                <div className="wp-nums">
                  <span className="grn">{(wp.radiant_win_prob * 100).toFixed(1)}% R</span>
                  <span className="dur">{wp.predicted_duration_min}m</span>
                  <span className="org">{((1 - wp.radiant_win_prob) * 100).toFixed(1)}% D</span>
                </div>
              </div>
            ) : <p className="muted">—</p>}
          </Card>

          <Card className="curve" title="Win vs. duration">
            {curve ? (
              <ResponsiveContainer width="100%" height={160}>
                <LineChart data={curve} margin={{ top: 6, right: 12, bottom: 0, left: -20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a3340" />
                  <XAxis dataKey="minute" unit="m" tick={{ fontSize: 11 }} />
                  <YAxis domain={[0, 1]} ticks={[0, 0.5, 1]} tick={{ fontSize: 11 }} tickFormatter={(v) => v.toFixed(1)} />
                  <Tooltip formatter={(v) => (v * 100).toFixed(1) + '%'} />
                  <ReferenceLine y={0.5} stroke="#c9d4e0" strokeDasharray="6 3" />
                  <Line type="monotone" dataKey="win" stroke="#16a34a" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : <p className="muted">—</p>}
          </Card>
        </div>

        <Card className="build" title="Item build"
          sub={`for ${focusSlot < 5 ? 'Radiant' : 'Dire'} slot ${(focusSlot % 5) + 1}`}>
          {draft[focusSlot] === 0 ? (
            <p className="muted">Pick your hero on the focused slot to see its build.</p>
          ) : out.build ? (
            <div>
              <div className="inv">
                {out.build.final_inventory.map((it) => <span key={it.item_id} className="chip">{it.item_name}</span>)}
              </div>
              <pre className="plan">{out.build.pretty}</pre>
            </div>
          ) : <p className="muted">—</p>}
        </Card>
      </main>
    </div>
  )
}

// ---------------- Combo discovery tab ----------------

function AttrTag({ a }) {
  return <span className="attr" style={{ background: ATTR_COLOR[a] || '#888' }}>{a}</span>
}

function DiscoverTab({ onAdd }) {
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
            <Th k="synergy">Synergy</Th><Th k="kpm">Kills/min</Th><Th k="fun">Fun</Th><th></th>
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
              <td><button className="add-draft" title="add to draft (Radiant)"
                onClick={() => onAdd(c.ids)}>＋ Draft</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {view.length > limit &&
        <button className="more" onClick={() => setLimit((l) => l + 150)}>show more</button>}
    </section>
  )
}

// ---------------- Screenshot labeling queue tab ----------------

function ShotsTab({ heroById, onReview }) {
  const [shots, setShots] = useState(null)
  const [err, setErr] = useState(null)
  const load = () => api.screenshots().then((r) => setShots(r.shots)).catch((e) => setErr(String(e)))
  useEffect(() => { load() }, [])

  if (err) return <p className="err">{err}</p>
  if (!shots) return <p>Loading screenshots…</p>

  const unlabeled = shots.filter((s) => !s.ground_truth).length
  const names = (ids) => ids.filter(Boolean).map((id) => heroById[id]?.name || `#${id}`).join(', ')

  return (
    <section className="shots-tab">
      <div className="disco-head">
        <div>
          <h2>Screenshot labeling queue</h2>
          <p className="sub">Every screenshot pasted on the Draft tab is saved here.
            {' '}{shots.length} saved · <b>{unlabeled} awaiting ground truth</b> — review one,
            fix the slots on the draft board, and confirm. Labels feed
            {' '}<code>scripts/eval_screenshot_detector.py</code>.</p>
        </div>
      </div>
      {shots.length === 0 && <p className="muted">Nothing yet — paste a screenshot on the Draft tab.</p>}
      <div className="shot-list">
        {shots.map((s) => (
          <div key={s.id} className={`shot-row ${s.ground_truth ? 'labeled' : ''}`}>
            <img src={`/api/screenshots/${s.id}/image`} loading="lazy" alt={s.id}
              onClick={() => window.open(`/api/screenshots/${s.id}/image`, '_blank')} />
            <div className="shot-info">
              <div className="shot-id">{s.created.replace('T', ' ').replace('Z', '')}
                {s.ground_truth
                  ? <span className="shot-badge ok">labeled · {s.labeled_by}</span>
                  : <span className="shot-badge">unlabeled</span>}
              </div>
              <div className="shot-heroes">
                detected: {s.detected?.detections?.length ?? 0}/10
                {s.detected?.detections?.length > 0 && <> — {names([...(s.detected.radiant || []), ...(s.detected.dire || [])])}</>}
              </div>
            </div>
            <div className="shot-actions">
              <button onClick={() => onReview(s)}>review on board</button>
              <button className="shot-del" title="delete screenshot"
                onClick={() => api.deleteScreenshot(s.id).then(load).catch((e) => setErr(String(e)))}>✕</button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

// ---------------- Feedback queue tab ----------------

const FB_CHIP = {
  captured: ['queued', 'wait'], transcribing: ['transcribing…', 'wait'],
  triaging: ['writing ticket…', 'wait'], triaged: ['awaiting approval', 'todo'],
  implementing: ['implementing…', 'wait'], implemented: ['ready to test', 'ok'],
  accepting: ['deploying…', 'wait'], done: ['done', 'done'],
  failed: ['failed', 'bad'], rejected: ['rejected', 'off'], discarded: ['discarded', 'off'],
}
const FB_BUSY = ['captured', 'transcribing', 'triaging', 'implementing', 'accepting']

function FeedbackComposer({ onSubmitted, recorder }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const { rec, secs, busy: recBusy, err: recErr, toggle } = recorder
  const canRecord = !!navigator.mediaDevices?.getUserMedia

  const submitText = async () => {
    if (!text.trim()) return
    setBusy(true); setErr(null)
    try { await api.feedbackText(text.trim()); setText(''); onSubmitted() }
    catch (e) { setErr(String(e)) }
    setBusy(false)
  }

  const showErr = err || recErr

  return (
    <div className="fb-composer card">
      <textarea value={text} placeholder="Describe an improvement — or hit the mic and just talk…"
        rows={2} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submitText() }} />
      <div className="fb-compose-row">
        {canRecord ? (
          <button className={`fb-mic ${rec ? 'rec' : ''}`} onClick={toggle} disabled={busy || recBusy}>
            {rec ? `■ stop (${secs}s)` : recBusy ? 'sending…' : '🎤 record'}
          </button>
        ) : (
          <span className="muted" title="getUserMedia needs HTTPS or localhost — open via localhost, or enable chrome://flags/#unsafely-treat-insecure-origin-as-secure for this origin">
            🎤 voice needs HTTPS / localhost
          </span>
        )}
        <span className="fb-spacer" />
        {showErr && <span className="err inline">{showErr}</span>}
        <button onClick={submitText} disabled={busy || !text.trim()}>
          {busy ? 'sending…' : 'Submit'}
        </button>
      </div>
    </div>
  )
}

function FbLog({ id, live }) {
  const [log, setLog] = useState('')
  const boxRef = useRef(null)
  useEffect(() => {
    let on = true
    const pull = () => api.feedbackLog(id).then((t) => { if (on) setLog(t) }).catch(() => {})
    pull()
    const t = live ? setInterval(pull, 3000) : null
    return () => { on = false; if (t) clearInterval(t) }
  }, [id, live])
  useEffect(() => { if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight }, [log])
  return <pre className="fb-log" ref={boxRef}>{log || '(no log yet)'}</pre>
}

function FeedbackItem({ item, onChanged, onErr }) {
  const [open, setOpen] = useState(false)
  const [showLog, setShowLog] = useState(false)
  const [acting, setActing] = useState(false)
  const [label, kind] = FB_CHIP[item.status] || [item.status, 'off']
  const t = item.ticket
  const busy = FB_BUSY.includes(item.status)
  const devUrl = item.status === 'implemented' && item.dev
    ? `http://${window.location.hostname}:${item.dev.port}/` : null

  const act = async (action, fn) => {
    setActing(true)
    try { await (fn ? fn() : api.feedbackAction(item.id, action)); onChanged() }
    catch (e) { onErr(String(e)) }
    setActing(false)
  }

  return (
    <div className={`fb-item ${item.status}`}>
      <div className="fb-row" onClick={() => setOpen((o) => !o)}>
        <span className={`fb-chip ${kind}`}>{busy && <i className="fb-spin" />}{label}</span>
        <span className="fb-title">{t?.title || (item.raw_text
          ? item.raw_text.slice(0, 90) + (item.raw_text.length > 90 ? '…' : '')
          : (item.source === 'voice' ? '🎤 voice memo (transcribing…)' : '…'))}</span>
        <span className="fb-when">{item.created.slice(5, 16).replace('T', ' ')}</span>
        <span className="fb-arrow">{open ? '▾' : '▸'}</span>
      </div>

      {item.error && <p className="err fb-err">{item.error}</p>}

      <div className="fb-actions" onClick={(e) => e.stopPropagation()}>
        {item.status === 'triaged' && <>
          <button disabled={acting} onClick={() => act('approve')}>✓ Approve — implement it</button>
          <button className="ghost" disabled={acting} onClick={() => act('reject')}>Reject</button>
        </>}
        {devUrl && <>
          <a className="fb-preview" href={devUrl} target="_blank" rel="noreferrer">⧉ Open dev preview :{item.dev.port}</a>
          <button disabled={acting} onClick={() => act('accept')}>✓ Accept & deploy</button>
          <button className="ghost danger" disabled={acting} onClick={() => act('discard')}>Discard</button>
        </>}
        {item.status === 'failed' && <>
          <button disabled={acting} onClick={() => act('retry')}>↻ Retry</button>
          <button className="ghost" disabled={acting} onClick={() => act('reject')}>Reject</button>
        </>}
        {['done', 'rejected', 'discarded'].includes(item.status) &&
          <button className="ghost danger" disabled={acting}
            onClick={() => act(null, () => api.deleteFeedback(item.id))}>✕ remove</button>}
        {(item.status === 'implementing' || item.status === 'accepting' || item.impl) &&
          <button className="ghost" onClick={() => setShowLog((s) => !s)}>
            {showLog ? 'hide log' : 'show log'}</button>}
      </div>

      {showLog && <FbLog id={item.id} live={busy} />}

      {open && (
        <div className="fb-detail">
          {t && <>
            <p className="fb-sum">{t.summary}</p>
            {t.details && <p className="fb-det">{t.details}</p>}
            {t.acceptance?.length > 0 && (
              <ul className="fb-acc">{t.acceptance.map((a, i) => <li key={i}>{a}</li>)}</ul>
            )}
          </>}
          {item.impl?.summary && (
            <div className="fb-implsum"><b>Implementation notes</b><pre>{item.impl.summary}</pre></div>
          )}
          {item.raw_text && <p className="fb-raw">“{item.raw_text}”
            {item.source === 'voice' && <audio controls preload="none" src={`/api/feedback/${item.id}/audio`} />}</p>}
          <p className="fb-meta">
            {item.branch && <>branch <code>{item.branch}</code> · </>}
            {item.merge_commit && <>merged <code>{item.merge_commit.slice(0, 8)}</code> · </>}
            {item.impl?.cost_usd != null && <>impl cost ${item.impl.cost_usd.toFixed(2)} · </>}
            id <code>{item.id}</code>
          </p>
        </div>
      )}
    </div>
  )
}

function FeedbackTab({ recorder }) {
  const [items, setItems] = useState(null)
  const [err, setErr] = useState(null)
  const [showArchive, setShowArchive] = useState(false)

  const load = () => api.feedback().then((r) => { setItems(r.items); setErr(null) })
    .catch((e) => setErr(String(e)))
  useEffect(() => {
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [])

  if (err && !items) return <p className="err">{err}</p>
  if (!items) return <p>Loading feedback queue…</p>

  const by = (...sts) => items.filter((i) => sts.includes(i.status))
  const groups = [
    ['Needs your attention', [...by('implemented'), ...by('failed')], 'act'],
    ['Awaiting approval', by('triaged'), 'act'],
    ['Working', by(...FB_BUSY), ''],
    ['Completed', by('done'), ''],
  ]
  const archive = by('rejected', 'discarded')

  return (
    <section className="fb-tab">
      <div className="disco-head">
        <div>
          <h2>Feedback → improvements</h2>
          <p className="sub">Talk or type an improvement. Claude turns it into a ticket; approving it
            spins up an implementation on a branch with a dev preview to test, and accepting deploys it here.</p>
        </div>
      </div>
      <FeedbackComposer onSubmitted={load} recorder={recorder} />
      {err && <p className="err">{err}</p>}
      {items.length === 0 && <p className="muted">Queue is empty — say what you wish this app did better.</p>}
      {groups.map(([name, list, cls]) => list.length > 0 && (
        <div key={name} className={`fb-group ${cls}`}>
          <h4>{name} <small>{list.length}</small></h4>
          {list.map((i) => <FeedbackItem key={i.id} item={i} onChanged={load} onErr={setErr} />)}
        </div>
      ))}
      {archive.length > 0 && (
        <div className="fb-group off">
          <h4 className="fb-archtoggle" onClick={() => setShowArchive((s) => !s)}>
            {showArchive ? '▾' : '▸'} Archive <small>{archive.length}</small></h4>
          {showArchive && archive.map((i) =>
            <FeedbackItem key={i.id} item={i} onChanged={load} onErr={setErr} />)}
        </div>
      )}
    </section>
  )
}

// ---------------- App shell ----------------

export default function App() {
  const [meta, setMeta] = useState(null)
  const [model, setModel] = useState(null)
  const [err, setErr] = useState(null)
  const [tab, setTab] = useState('draft')
  const [patch, setPatch] = useState(null)
  const [draft, setDraft] = useState(SAMPLE)
  const [pendingShot, setPendingShot] = useState(null)
  const [fbCount, setFbCount] = useState(0)
  const [apiStatus, setApiStatus] = useState('unknown')   // 'unknown' | 'ok' | 'error'

  // Recording state lives here (not in FeedbackComposer) so switching tabs
  // never tears down the MediaRecorder mid-recording.
  const [rec, setRec] = useState(false)
  const [recSecs, setRecSecs] = useState(0)
  const [recBusy, setRecBusy] = useState(false)
  const [recErr, setRecErr] = useState(null)
  const recRef = useRef(null)

  useEffect(() => {
    if (!rec) return
    const t = setInterval(() => setRecSecs((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [rec])

  const toggleRec = async () => {
    if (rec) { recRef.current?.stop(); return }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : ''
      const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : {})
      const chunks = []
      let lostMic = false
      // Fires only when the mic dies externally (permission revoked, device
      // unplugged) — not when we stop the tracks ourselves below.
      stream.getTracks().forEach((t) => {
        t.onended = () => { lostMic = true; setRecErr('microphone lost mid-recording — audio discarded') }
      })
      mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setRec(false); setRecSecs(0)
        if (lostMic) return
        setRecBusy(true)
        try { await api.feedbackAudio(new Blob(chunks, { type: mr.mimeType || 'audio/webm' })) }
        catch (e) { setRecErr(String(e)) }
        setRecBusy(false)
      }
      recRef.current = mr
      mr.start()
      setRec(true); setRecSecs(0); setRecErr(null)
    } catch (e) { setRecErr(`mic unavailable: ${e.message}`) }
  }

  const recorder = { rec, secs: recSecs, busy: recBusy, err: recErr, toggle: toggleRec }

  useEffect(() => {
    const probe = () => api.health().then(() => setApiStatus('ok')).catch(() => setApiStatus('error'))
    probe()
    const t = setInterval(probe, 20000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    const pull = () => api.feedback().then((r) =>
      setFbCount(r.items.filter((i) => ['triaged', 'implemented', 'failed'].includes(i.status)).length))
      .catch(() => {})
    pull()
    const t = setInterval(pull, 30000)
    return () => clearInterval(t)
  }, [])

  const reviewShot = (s) => {
    // load the shot's detections (or its label, if revisiting) onto the board
    const src = s.ground_truth || s.detected
    setDraft([...(src.radiant || []), ...(src.dire || [])].slice(0, 10))
    setPendingShot(s.id)
    setTab('draft')
  }

  const addCombo = (ids) => {
    const nd = Array(10).fill(0)
    ids.slice(0, 5).forEach((id, i) => { nd[i] = id })   // combo onto Radiant, rest masked
    setDraft(nd)
    setTab('draft')
  }

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setErr(String(e)))
    api.model().then(setModel).catch(() => {})
    api.patchStatus().then(setPatch).catch(() => {})
  }, [])

  if (err && !meta) return <div className="app"><p className="err">Cannot reach API: {err}</p></div>
  if (!meta) return <div className="app"><p>Loading…</p></div>

  const newPatches = patch?.new_patches ?? []

  return (
    <div className="app">
      {newPatches.length > 0 && (
        <div className="patch-banner">
          ⚠ New Dota patch detected: {newPatches.map((p) => `${p.name} (${p.date})`).join(', ')}
          {' '}— not yet in the edge list. Run <code>python -m dotaml_live.pipeline.patch_watch --add</code>.
        </div>
      )}
      <header>
        <h1>dotaml-live <span className="tag">Turbo</span></h1>
        <nav className="tabs">
          <button className={tab === 'draft' ? 'on' : ''} onClick={() => setTab('draft')}>Draft analysis</button>
          <button className={tab === 'discover' ? 'on' : ''} onClick={() => setTab('discover')}>Combo discovery</button>
          <button className={tab === 'shots' ? 'on' : ''} onClick={() => setTab('shots')}>Screenshots</button>
          <button className={tab === 'feedback' ? 'on' : ''} onClick={() => setTab('feedback')}>
            Feedback{rec && <span className="rec-dot" title="recording in progress" />}
            {fbCount > 0 && <span className="fb-badge">{fbCount}</span>}
          </button>
        </nav>
        <span className="model">
          <span className={`status-dot ${apiStatus}`}
            title={apiStatus === 'ok' ? 'API connected' : apiStatus === 'error' ? 'API unreachable' : 'API status unknown'} />
          model: {model?.version ?? '?'} · {model?.device ?? '?'}
        </span>
      </header>
      {tab === 'draft' && <DraftTab meta={meta} draft={draft} setDraft={setDraft} nHeroes={model?.n_heroes}
        pendingShot={pendingShot} setPendingShot={setPendingShot} />}
      {tab === 'discover' && <DiscoverTab onAdd={addCombo} />}
      {tab === 'shots' && <ShotsTab onReview={reviewShot}
        heroById={Object.fromEntries(meta.heroes.map((h) => [h.id, h]))} />}
      {/* Feedback stays mounted (just hidden) so the composer survives tab switches */}
      <div style={tab === 'feedback' ? undefined : { display: 'none' }}>
        <FeedbackTab recorder={recorder} />
      </div>
    </div>
  )
}
