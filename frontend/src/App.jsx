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

// ---- server-synced persistent state (data/settings.json via /api/settings) ----
// localStorage is per-origin, so anything kept only there is "lost" on every
// dev-preview port. The server file is shared by the main app and all previews.
function useServerSetting(key, initial) {
  const lsKey = `dl.${key}`
  const [v, setV] = useState(() => {
    try { const s = localStorage.getItem(lsKey); return s ? JSON.parse(s) : initial } catch { return initial }
  })
  const ready = useRef(false)
  useEffect(() => {
    let on = true
    api.settings().then((s) => {
      if (!on) return
      ready.current = true
      if (s[key] != null) setV(s[key])
      else setV((cur) => {   // first run after upgrade: push existing local value up
        if (JSON.stringify(cur) !== JSON.stringify(initial)) api.saveSettings({ [key]: cur }).catch(() => {})
        return cur
      })
    }).catch(() => { ready.current = true })
    return () => { on = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  useEffect(() => {
    try { localStorage.setItem(lsKey, JSON.stringify(v)) } catch {}
    if (!ready.current) return
    const t = setTimeout(() => api.saveSettings({ [key]: v }).catch(() => {}), 400)
    return () => clearTimeout(t)
  }, [lsKey, key, v])
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

// ---- screenshot → draft (capture the Dota monitor via the Screen Capture API) ----
// The dashboard server is headless, so the grab happens in this browser, on the
// machine that actually runs Dota. The first capture opens the browser's screen
// picker (it lists every monitor) — pick the Dota one. The stream is then kept
// alive at module scope, so every later capture is instant with no prompt until
// the page closes or you hit stop. After a fill, the shot sits in the server's
// labeling queue; fixing the slots and hitting ✓ stores it as ground truth.
let screenStream = null   // survives tab switches / component re-mounts

function ScreenshotFill({ onDraft, draft, pending, setPending }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [sharing, setSharing] = useState(() => !!screenStream?.active)
  const busyRef = useRef(false)
  const canCapture = !!navigator.mediaDevices?.getDisplayMedia

  const dropStream = () => {
    if (screenStream) screenStream.getTracks().forEach((t) => t.stop())
    screenStream = null
    setSharing(false)
  }

  const acquire = async (repick) => {
    if (repick) dropStream()
    if (screenStream?.active) return screenStream
    const s = await navigator.mediaDevices.getDisplayMedia({
      video: { displaySurface: 'monitor', frameRate: 5,
               width: { ideal: 3840 }, height: { ideal: 2160 } },  // native res, never upscaled
      audio: false,
      selfBrowserSurface: 'exclude',
      surfaceSwitching: 'exclude',
      monitorTypeSurfaces: 'include',
    })
    // user hit the browser's own "stop sharing" bar
    s.getVideoTracks()[0].addEventListener('ended', () => { screenStream = null; setSharing(false) })
    screenStream = s
    setSharing(true)
    return s
  }

  const grabFrame = async (stream) => {
    const video = document.createElement('video')
    video.srcObject = stream
    video.muted = true
    video.playsInline = true
    await video.play()
    if (video.requestVideoFrameCallback) await new Promise((res) => video.requestVideoFrameCallback(res))
    else await new Promise((res) => setTimeout(res, 350))
    const c = document.createElement('canvas')
    c.width = video.videoWidth
    c.height = video.videoHeight
    c.getContext('2d').drawImage(video, 0, 0)
    video.pause()
    video.srcObject = null   // release the element but keep the stream for next time
    return new Promise((res) => c.toBlob(res, 'image/png'))
  }

  const capture = async (repick = false) => {
    if (busyRef.current || !canCapture) return
    busyRef.current = true
    setBusy(true); setMsg(null); setPending(null)
    try {
      const blob = await grabFrame(await acquire(repick))
      const r = await api.draftFromScreenshot(blob)
      const n = r.detections.length
      onDraft([...r.radiant, ...r.dire])
      if (r.shot_id && !r.already_labeled) setPending(r.shot_id)
      setMsg(n
        ? { ok: true, text: `found ${n}/10 heroes in ${(r.elapsed_ms / 1000).toFixed(1)}s` }
        : { ok: false, text: 'no heroes found — is the draft on the shared monitor?' })
    } catch (e) {
      if (e?.name === 'NotAllowedError') setMsg({ ok: false, text: 'screen share cancelled' })
      else setMsg({ ok: false, text: String(e) })
    }
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

  const monLabel = screenStream?.getVideoTracks?.()[0]?.label || 'monitor'
  return (
    <div className={`shot ${busy ? 'busy' : ''}`}>
      <button className="shot-cap" disabled={busy || !canCapture} onClick={() => capture(false)}>
        {busy ? '⏳ capturing…' : '📸 Capture screen'}
      </button>
      {!canCapture && (
        <span className="shot-hint warn-hint"
          title="getDisplayMedia needs HTTPS or localhost — open via localhost, or enable chrome://flags/#unsafely-treat-insecure-origin-as-secure for this origin">
          ⚠ screen capture unavailable on this origin
        </span>
      )}
      {canCapture && !sharing && !busy && !pending && (
        <span className="shot-hint">first capture asks which monitor — pick the Dota one, it's remembered</span>
      )}
      {canCapture && sharing && !busy && !pending && (
        <span className="shot-hint">sharing <b title={monLabel}>{monLabel}</b>
          <button className="shot-link" title="pick a different monitor" onClick={() => capture(true)}>change</button>
          <button className="shot-link" title="stop sharing" onClick={dropStream}>stop</button>
        </span>
      )}
      {msg && <span className={`shot-msg ${msg.ok ? 'ok' : 'bad'}`}>{msg.text}</span>}
      {pending && !busy && (
        <span className="shot-confirm">
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

// split 5 heroes into mid solo + two duos, maximizing summed pair synergy.
// pins ({ safe: [ids], mid: [ids], off: [ids] }) locks heroes to specific
// lanes: pinned heroes are placed first and the search optimises only the
// remaining free slots. midScore (hero id -> rank) breaks exact synergy ties —
// prefer a core-shaped hero mid and keep supports in a duo lane.
// 5 mid choices x 3 splits x 2 lane assignments (fewer when pinned).
const LANE_CAP = { safe: 2, mid: 1, off: 2 }
function resolveLanes(ids, synOf, pins, midScore) {
  // excess pins beyond a lane's capacity are ignored (oldest first kept)
  const pin = {}
  for (const lane of ['safe', 'mid', 'off']) {
    pin[lane] = pins[lane].slice(0, LANE_CAP[lane])
    if (pins[lane].length > LANE_CAP[lane])
      console.warn(`resolveLanes: ${lane} lane holds ${LANE_CAP[lane]} — ignoring excess pins`,
        pins[lane].slice(LANE_CAP[lane]))
  }
  let best = null
  for (const mid of ids) {
    if (pin.mid.length && mid !== pin.mid[0]) continue
    if (pin.safe.includes(mid) || pin.off.includes(mid)) continue
    const rest = ids.filter((x) => x !== mid)
    for (let j = 1; j < rest.length; j++) {
      const a = [rest[0], rest[j]]
      const b = rest.filter((x) => x !== a[0] && x !== a[1])
      const sa = synOf(a[0], a[1]), sb = synOf(b[0], b[1])
      const score = (sa ?? 0) + (sb ?? 0)
      // the stronger duo prefers the safe lane; pins may force the swap
      const order = (sa ?? 0) >= (sb ?? 0)
        ? [[a, sa, b, sb], [b, sb, a, sa]]
        : [[b, sb, a, sa], [a, sa, b, sb]]
      for (const [safeIds, ssyn, offIds, osyn] of order) {
        if (!pin.safe.every((id) => safeIds.includes(id))) continue
        if (!pin.off.every((id) => offIds.includes(id))) continue
        if (best && score < best.score) break
        if (best && score === best.score
            && (midScore ? midScore(mid) : 0) <= (midScore ? midScore(best.mid) : 0)) break
        best = { mid, safe: { ids: safeIds, syn: ssyn }, off: { ids: offIds, syn: osyn }, score }
        break // first valid assignment in preference order wins for this split
      }
    }
  }
  return best
}

const fmtSyn = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%')

function PinIcon({ filled }) {
  return (
    <svg className={`pin-ico ${filled ? 'filled' : ''}`} viewBox="0 0 24 24" width="12" height="12"
      fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 4v6l-2 4v2h10v-2l-2-4V4" />
      <line x1="12" y1="16" x2="12" y2="21" />
      <line x1="8" y1="4" x2="16" y2="4" />
    </svg>
  )
}

function CurveTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null
  return (
    <div className="curve-tip">{label}m → {(payload[0].value * 100).toFixed(1)}%</div>
  )
}

function DraftTab({ meta, draft, setDraft, nHeroes, pendingShot, setPendingShot }) {
  const heroes = useMemo(
    () => [...meta.heroes].sort((a, b) => a.name.localeCompare(b.name)), [meta])
  const heroById = useMemo(() => {
    const m = {}; meta.heroes.forEach((h) => { m[h.id] = h }); return m
  }, [meta])

  const [mySide, setMySide] = usePersist('dl.side', 'radiant')
  const [focusSlot, setFocusSlot] = useState(0)
  const [slotPlayer, setSlotPlayer] = useState(Array(10).fill(null))
  const [players, setPlayers] = useServerSetting('players', DEFAULT_PLAYERS)
  const [favorites, setFavorites] = usePersist('dl.favs', [])
  const [auto, setAuto] = usePersist('dl.auto', true)
  const [settings, setSettings] = useState(false)

  const [out, setOut] = useState({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const seq = useRef(0)

  // lane resolver: pair synergies from the precomputed combos table
  const [combosTbl, setCombosTbl] = useState(null)
  // per-lane pins: hero ids locked to a lane; everyone else auto-resolves
  const [pinnedLanes, setPinnedLanes] = useState({ safe: [], mid: [], off: [] })
  useEffect(() => { api.combosTable().then(setCombosTbl).catch(() => {}) }, [])
  const pairSyn = useMemo(() => {
    const m = new Map()
    for (const c of combosTbl?.combos || [])
      if (c.ids.length === 2) m.set([...c.ids].sort((a, b) => a - b).join('-'), c.synergy)
    return m
  }, [combosTbl])
  const myIds = (mySide === 'radiant' ? RAD : DIRE).map((i) => draft[i]).filter(Boolean)
  const lanes = useMemo(() => {
    if (myIds.length !== 5 || new Set(myIds).size !== 5) return null
    const synOf = (a, b) => pairSyn.get((a < b ? [a, b] : [b, a]).join('-')) ?? null
    // drop pins for heroes no longer in the draft
    const pins = {
      safe: pinnedLanes.safe.filter((id) => myIds.includes(id)),
      mid: pinnedLanes.mid.filter((id) => myIds.includes(id)),
      off: pinnedLanes.off.filter((id) => myIds.includes(id)),
    }
    // role tiebreaker for equal-synergy splits: supports belong in a duo lane
    const midScore = (id) => {
      const r = heroById[id]?.roles || []
      return (r.includes('Carry') || r.includes('Nuker') ? 1 : 0) - (r.includes('Support') ? 2 : 0)
    }
    return resolveLanes(myIds, synOf, pins, midScore)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(myIds), pairSyn, JSON.stringify(pinnedLanes), heroById])

  // pin a hero to a lane: one lane per hero, capacity-capped (oldest pin evicted)
  const pinTo = (lane, id) => setPinnedLanes((p) => {
    const next = {
      safe: p.safe.filter((x) => x !== id),
      mid: p.mid.filter((x) => x !== id),
      off: p.off.filter((x) => x !== id),
    }
    next[lane] = [...next[lane], id]
    while (next[lane].length > LANE_CAP[lane]) {
      const out = next[lane].shift()
      console.warn(`${lane} lane holds ${LANE_CAP[lane]} — unpinned hero #${out}`)
    }
    return next
  })
  const unpin = (id) => setPinnedLanes((p) => ({
    safe: p.safe.filter((x) => x !== id),
    mid: p.mid.filter((x) => x !== id),
    off: p.off.filter((x) => x !== id),
  }))
  const [dragLane, setDragLane] = useState(null)   // lane key under an active drag

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
            <p className="hint">Set each account ID once — used for personalized predictions.
              Saved on the server, so they survive reloads, restarts, and dev previews.</p>
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

        <Card className="lanes" title="Lanes"
          sub={`max pair synergy · your team (${mySide})`}>
          {!lanes ? (
            <p className="muted">Draft all five {mySide} heroes (no duplicates) to auto-resolve lanes.</p>
          ) : (
            <div className="lane-list">
              {[
                ['Safe lane', 'safe', lanes.safe.ids, lanes.safe.syn],
                ['Mid', 'mid', [lanes.mid], null],
                ['Off lane', 'off', lanes.off.ids, lanes.off.syn],
              ].map(([label, lane, ids, syn]) => {
                const isMid = lane === 'mid'
                return (
                  <div key={lane} className={`lane ${isMid ? 'mid' : ''} ${dragLane === lane ? 'drag-over' : ''}`}
                    onDragOver={(e) => { e.preventDefault(); setDragLane(lane) }}
                    onDragLeave={() => setDragLane((l) => (l === lane ? null : l))}
                    onDrop={(e) => {
                      e.preventDefault(); setDragLane(null)
                      const id = Number(e.dataTransfer.getData('text/plain'))
                      if (myIds.includes(id)) pinTo(lane, id)
                    }}>
                    <div className="lane-heroes">
                      {ids.map((id) => {
                        const h = heroById[id]
                        const pinned = pinnedLanes[lane].includes(id)
                        return (
                          <span key={id} className={`lane-hero ${pinned ? 'pinned' : ''}`} draggable
                            title="drag to a lane to pin it there"
                            onDragStart={(e) => e.dataTransfer.setData('text/plain', String(id))}>
                            <i className="dot" style={{ background: ATTR_COLOR[h?.attr || '?'] }} />
                            {h?.name || `#${id}`}
                            {pinned ? (
                              <button className="pin-btn" title={`unpin — back to auto-resolved ${label.toLowerCase()}`}
                                onClick={() => unpin(id)}><PinIcon filled /></button>
                            ) : (
                              <button className="pin-btn" title={`pin ${h?.name || 'this hero'} to ${label.toLowerCase()} and auto-resolve the rest`}
                                onClick={() => pinTo(lane, id)}><PinIcon /></button>
                            )}
                          </span>
                        )
                      })}
                    </div>
                    <span className={`lane-syn ${syn == null ? '' : syn >= 0 ? 'pos' : 'neg'}`}
                      title={isMid ? 'solo lane' : syn == null ? 'no synergy data for this pair' : 'pair synergy'}>
                      {isMid ? (pinnedLanes.mid.includes(lanes.mid) ? 'pinned' : 'auto') : fmtSyn(syn)}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
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

          <Card className="curve" title="Radiant win vs. duration">
            {curve ? (
              <ResponsiveContainer width="100%" height={160}>
                <LineChart data={curve} margin={{ top: 6, right: 12, bottom: 0, left: -20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a3340" />
                  <XAxis dataKey="minute" unit="m" tick={{ fontSize: 11 }} />
                  <YAxis domain={[0, 1]} ticks={[0, 0.5, 1]} tick={{ fontSize: 11 }} tickFormatter={(v) => v.toFixed(1)} />
                  <Tooltip cursor={{ stroke: '#2a3340' }} content={<CurveTooltip />} />
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
              <div className="plan">
                {out.build.actions.map((a, i) => (
                  <div key={i} className="plan-row" style={a.kind === 'sell' ? { color: '#c8943a' } : undefined}>
                    <span className="plan-min">{a.minute}m</span>
                    <span className="plan-kind">{a.kind}</span>
                    <span className="plan-item">{a.item_name}</span>
                    <span className="plan-gold">({a.gold_delta >= 0 ? '+' : ''}{a.gold_delta}g)</span>
                  </div>
                ))}
              </div>
            </div>
          ) : <p className="muted">—</p>}
        </Card>
      </main>
    </div>
  )
}

// ---------------- Combo discovery tab ----------------

// flag combos whose side-averaged win rate is below par, even with positive synergy
const LOW_WINRATE_THRESHOLD = 0.50

function AttrTag({ a }) {
  return <span className="attr" style={{ background: ATTR_COLOR[a] || '#888' }}>{a}</span>
}

// per-metric minimum filters: [key, label, formatter] — "energy" in the feedback is the synergy score
const METRIC_FILTERS = [
  ['avg_winprob', 'Win rate', (v) => (v * 100).toFixed(1) + '%'],
  ['synergy', 'Synergy', (v) => (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%'],
  ['kpm', 'Kills/min', (v) => v.toFixed(2)],
  ['fun', 'Fun', (v) => v.toFixed(2)],
]
const METRIC_FMT = Object.fromEntries(METRIC_FILTERS.map(([k, , f]) => [k, f]))

// percentile of x against a p0..p100 quantile grid (same interpolation as the
// backend's combo_explain._percentile)
function pctFromGrid(q, x) {
  let i = 0
  while (i < q.length && q[i] <= x) i++
  if (i === 0) return 0
  if (i >= q.length) return 100
  const lo = q[i - 1], hi = q[i]
  return ((i - 1 + (hi <= lo ? 0 : (x - lo) / (hi - lo))) * 100) / (q.length - 1)
}

// exact percentile rank of x within sorted values (for stats without a grid)
function pctFromSorted(vals, x) {
  let lo = 0, hi = vals.length
  while (lo < hi) { const m = (lo + hi) >> 1; if (vals[m] <= x) lo = m + 1; else hi = m }
  return (lo / vals.length) * 100
}

function MinSlider({ label, min, max, value, format, disabled, onChange }) {
  const step = (max - min) / 200 || 0.001
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0
  return (
    <div className={`rslider ${disabled ? 'off' : ''}`}>
      <div className="rs-head">
        <span>{label}</span>
        <span className="rs-vals">≥ {format(value)}</span>
      </div>
      <div className="rs-track">
        <div className="rs-fill" style={{ left: `${pct}%`, width: `${100 - pct}%` }} />
        <input type="range" min={min} max={max} step={step} value={value} disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))} />
      </div>
    </div>
  )
}

// solo baseline win rate for the picked hero (all other 9 slots masked, Radiant/Dire averaged)
function HeroStatCard({ name, attr, stats }) {
  const avg = stats?.win_rate_avg
  const kpm = stats?.kills_per_min
  return (
    <div className="hero-stat">
      <div className="hs-head">
        <i className="dot" style={{ background: ATTR_COLOR[attr] || '#888' }} />
        <span className="hs-name">{name}</span>
        <span className="hs-sub">solo win rate · 9 slots masked, R/D averaged</span>
      </div>
      {stats?.loading && <p className="muted">computing…</p>}
      {stats?.error && <span className="err inline">{stats.error}</span>}
      {avg != null && (
        <>
          <div className="hs-main">
            <b className={avg >= 0.5 ? 'pos' : 'neg'}>{(avg * 100).toFixed(1)}%</b>
            <div className="hs-bar">
              <div className={avg >= 0.5 ? 'pos' : 'neg'} style={{ width: `${avg * 100}%` }} />
            </div>
          </div>
          <div className="hs-sides">
            <span>Radiant <b>{(stats.win_rate_radiant * 100).toFixed(1)}%</b></span>
            <span>Dire <b>{(stats.win_rate_dire * 100).toFixed(1)}%</b></span>
          </div>
          {kpm != null && Number.isFinite(kpm) && (
            <div className="hs-kpm">
              <span>kills+assists / min (solo baseline)</span>
              <b>{kpm.toFixed(2)}</b>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function DiscoverTab({ onAdd, meta, nHeroes }) {
  const heroes = useMemo(
    () => [...meta.heroes].sort((a, b) => a.name.localeCompare(b.name)), [meta])
  const heroById = useMemo(() => {
    const m = {}; meta.heroes.forEach((h) => { m[h.id] = h }); return m
  }, [meta])
  const [hero, setHero] = useState(0)             // picked hero id (0 = none)
  const [stats, setStats] = useState(null)        // {win_rate_*} | {loading} | {error}
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [q, setQ] = useState('')
  const [size, setSize] = useState('pairs')      // 'pairs' | 'trios'
  const [sortKey, setSortKey] = useState('fun')
  const [limit, setLimit] = useState(150)
  const [mins, setMins] = useState({})            // metric key -> min value; absent = no filter
  const [explain, setExplain] = useState(null)    // {names, loading, text?, error?} -> modal

  const explainCombo = (c) => {
    if (explain?.loading) return
    setExplain({ names: c.names, loading: true })
    api.explainCombo({ heroes: c.names, synergy: c.synergy, avg_winprob: c.avg_winprob, kpm: c.kpm })
      .then((r) => setExplain((e) => e && { names: e.names, loading: false, text: r.explanation }))
      .catch((err) => {
        // api.post throws `path -> status {"detail": "..."}`; surface the detail if present
        const m = String(err.message || err).match(/"detail"\s*:\s*"([^"]+)"/)
        setExplain((e) => e && { names: e.names, loading: false, error: m ? m[1] : String(err.message || err) })
      })
  }

  useEffect(() => { api.combosTable().then(setData).catch((e) => setErr(String(e))) }, [])
  useEffect(() => { setLimit(150) }, [size, q, sortKey, mins, hero])
  useEffect(() => { setMins({}) }, [size])

  // heroes past the model's vocabulary have no meaningful prediction — skip stats
  const heroUnsupported = hero !== 0 && nHeroes != null && hero >= nHeroes
  useEffect(() => {
    if (!hero || heroUnsupported) { setStats(null); return }
    let on = true
    setStats({ loading: true })
    api.heroStats(hero)
      .then((r) => { if (on) setStats(r) })
      .catch((e) => { if (on) setStats({ error: String(e) }) })
    return () => { on = false }
  }, [hero, heroUnsupported])

  const base = data ? (size === 'pairs' ? data.combos : data.trios) || [] : []
  const rows = useMemo(() => {
    if (!base.length) return []
    const syn = base.map((c) => c.synergy), kpm = base.map((c) => c.kpm)
    const sMin = Math.min(...syn), sMax = Math.max(...syn), kMin = Math.min(...kpm), kMax = Math.max(...kpm)
    const nrm = (x, lo, hi) => (hi > lo ? (x - lo) / (hi - lo) : 0)
    return base.map((c) => ({ ...c, fun: nrm(c.synergy, sMin, sMax) + nrm(c.kpm, kMin, kMax) }))
  }, [base])

  // slider bounds follow the loaded data for the current size (pairs/trios)
  const bounds = useMemo(() => {
    const b = {}
    for (const [k] of METRIC_FILTERS) {
      const vals = rows.map((c) => c[k]).filter((v) => v != null)
      b[k] = vals.length ? [Math.min(...vals), Math.max(...vals)] : [0, 1]
    }
    return b
  }, [rows])

  // hover tooltip placing a stat value in its distribution. Synergy/kpm use the
  // server quantile grids (full-distribution trio synergy; trio kpm covers only
  // the kept slice); win rate and fun rank against the loaded rows.
  const statTip = useMemo(() => {
    const grids = { synergy: data?.synergy_scale?.[size], kpm: data?.kpm_scale?.[size] }
    const sorted = {}
    for (const [k] of METRIC_FILTERS)
      sorted[k] = rows.map((c) => c[k]).filter((v) => v != null).sort((a, b) => a - b)
    return (k, v) => {
      if (v == null) return undefined
      const g = grids[k]
      if (!g && !sorted[k].length) return undefined
      const pct = g ? pctFromGrid(g.q, v) : pctFromSorted(sorted[k], v)
      const median = g ? g.q[50] : sorted[k][Math.floor(sorted[k].length / 2)]
      const n = g ? g.n : sorted[k].length
      const pool = size === 'pairs' ? `all ${n.toLocaleString()} pairs`
        : (g && g.n === data?.n_trios_scored) ? `all ${n.toLocaleString()} trios`
        : `${n.toLocaleString()} tracked trios`
      return `${pct.toFixed(1)}th percentile of ${pool} · median ${METRIC_FMT[k](median)}`
    }
  }, [data, rows, size])

  // a slider at the data minimum is a no-op, never a filter
  const minActive = (k) => mins[k] != null && mins[k] > bounds[k][0]
  const nActive = METRIC_FILTERS.filter(([k]) => minActive(k)).length

  const view = useMemo(() => {
    const needle = q.trim().toLowerCase()
    let r = rows
    for (const [k] of METRIC_FILTERS) {
      if (mins[k] == null || mins[k] <= bounds[k][0]) continue
      r = r.filter((c) => c[k] != null && c[k] >= mins[k])
    }
    if (hero) r = r.filter((c) => c.ids.includes(hero))
    if (needle) r = r.filter((c) => c.names.some((n) => n.toLowerCase().includes(needle)))
    return [...r].sort((x, y) => y[sortKey] - x[sortKey])
  }, [rows, q, sortKey, mins, bounds, hero])

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
      <div className="disco-hero">
        <div className="dh-pick">
          <span className="dh-lbl">Hero</span>
          <HeroCombo heroes={heroes} value={hero} onChange={setHero} nHeroes={nHeroes}
            placeholder="— pick a hero —" onTabCommit={() => {}} />
        </div>
        {hero !== 0 && !heroUnsupported &&
          <HeroStatCard name={heroById[hero]?.name} attr={heroById[hero]?.attr} stats={stats} />}
      </div>
      <div className="disco-filters">
        {METRIC_FILTERS.map(([k, label, fmt]) => (
          <MinSlider key={k} label={label} min={bounds[k][0]} max={bounds[k][1]}
            value={mins[k] != null ? mins[k] : bounds[k][0]} format={fmt} disabled={!rows.length}
            onChange={(v) => setMins((m) => ({ ...m, [k]: v }))} />
        ))}
        <button className="rs-reset" disabled={!nActive} onClick={() => setMins({})}>Reset</button>
      </div>
      <table className="combos">
        <thead>
          <tr>
            <th>#</th><th>Combo</th>
            <Th k="synergy">Synergy</Th><th>Win rate</th><Th k="kpm">Kills/min</Th><Th k="fun">Fun</Th><th></th>
          </tr>
        </thead>
        <tbody>
          {view.slice(0, limit).map((c, i) => {
            const lowWr = c.avg_winprob != null && c.avg_winprob < LOW_WINRATE_THRESHOLD
            return (
              <tr key={c.ids.join('-')} className={lowWr ? 'low-wr' : ''}>
                <td className="rank">{i + 1}</td>
                <td className="combo">
                  {c.names.map((n, j) => (
                    <span key={j}>{j > 0 && <span className="plus">+</span>}<AttrTag a={c.attrs[j]} /> {n} </span>
                  ))}
                </td>
                <td className={`stat ${c.synergy >= 0 ? 'pos' : 'neg'}`}
                  title={statTip('synergy', c.synergy)}>{c.synergy >= 0 ? '+' : ''}{(c.synergy * 100).toFixed(2)}%</td>
                <td className={`stat ${lowWr ? 'wr-low' : ''}`} title={statTip('avg_winprob', c.avg_winprob)}>
                  {c.avg_winprob != null ? (c.avg_winprob * 100).toFixed(1) + '%' : '—'}
                  {lowWr && <span className="wr-flag"
                    title={`combined win rate below ${LOW_WINRATE_THRESHOLD * 100}% despite synergy`}>⚠</span>}
                </td>
                <td className="stat" title={statTip('kpm', c.kpm)}>{c.kpm.toFixed(2)}</td>
                <td className="stat" title={statTip('fun', c.fun)}>
                  <div className="funbar"><div style={{ width: `${(c.fun / 2) * 100}%` }} /></div></td>
                <td className="row-actions"><button className="add-draft" title="add to draft (Radiant)"
                  onClick={() => onAdd(c.ids)}>＋ Draft</button>
                  <button className="explain-btn" title="ask Claude why this combo works"
                    disabled={explain?.loading} onClick={() => explainCombo(c)}>✨</button></td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {view.length > limit &&
        <button className="more" onClick={() => setLimit((l) => l + 150)}>show more</button>}
      {explain && (
        <div className="modal-overlay" onClick={() => setExplain(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>✨ {explain.names.join(' + ')}</h3>
              <button className="modal-close" title="close" onClick={() => setExplain(null)}>✕</button>
            </div>
            {explain.loading && <p className="modal-wait"><span className="spinner" /> Asking Claude…</p>}
            {explain.text && <p className="modal-text">{explain.text}</p>}
            {explain.error && (
              <div>
                <p className="err">Could not generate explanation</p>
                <p className="modal-err-detail">{explain.error}</p>
              </div>
            )}
          </div>
        </div>
      )}
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
  resolving: ['resolving conflicts…', 'wait'],
  accepting: ['deploying…', 'wait'], done: ['done', 'done'],
  failed: ['failed', 'bad'], rejected: ['rejected', 'off'], discarded: ['discarded', 'off'],
}
const FB_BUSY = ['captured', 'transcribing', 'triaging', 'implementing', 'resolving', 'accepting']
const FB_EDITABLE = ['triaged', 'failed', 'implemented']   // settled, runner not active
const FB_MODELS = ['sonnet', 'opus', 'haiku']
const FB_EFFORTS = ['low', 'medium', 'high']               // timeout mapping is server-side
const FB_AREAS = ['frontend', 'backend', 'model', 'pipeline', 'other']

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

function FbCommentComposer({ id, onSubmitted }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [rec, setRec] = useState(false)
  const [secs, setSecs] = useState(0)
  const [recBusy, setRecBusy] = useState(false)
  const mrRef = useRef(null)
  const canRecord = !!navigator.mediaDevices?.getUserMedia

  useEffect(() => {
    if (!rec) return
    const t = setInterval(() => setSecs((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [rec])

  const submitText = async () => {
    if (!text.trim()) return
    setBusy(true); setErr(null)
    try { await api.feedbackComment(id, text.trim()); setText(''); onSubmitted() }
    catch (e) { setErr(String(e)) }
    setBusy(false)
  }

  const toggle = async () => {
    if (rec) { mrRef.current?.stop(); return }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : ''
      const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : {})
      const chunks = []
      let lostMic = false
      stream.getTracks().forEach((t) => {
        t.onended = () => { lostMic = true; setErr('microphone lost mid-recording — audio discarded') }
      })
      mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setRec(false); setSecs(0)
        if (lostMic) return
        setRecBusy(true)
        try { await api.feedbackCommentAudio(id, new Blob(chunks, { type: mr.mimeType || 'audio/webm' })); onSubmitted() }
        catch (e) { setErr(String(e)) }
        setRecBusy(false)
      }
      mrRef.current = mr
      mr.start()
      setRec(true); setSecs(0); setErr(null)
    } catch (e) { setErr(`mic unavailable: ${e.message}`) }
  }

  return (
    <div className="fb-composer fb-comment-composer">
      <textarea value={text} placeholder="Comment on this ticket — or hit the mic and just talk…"
        rows={2} autoFocus onChange={(e) => setText(e.target.value)}
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
        {err && <span className="err inline">{err}</span>}
        <button onClick={submitText} disabled={busy || !text.trim()}>
          {busy ? 'sending…' : 'Submit'}
        </button>
      </div>
    </div>
  )
}

function ImplDefaults({ defaults, onSave }) {
  return (
    <div className="fb-defaults card">
      <span className="fb-defaults-label">Implementation defaults</span>
      <label>model
        <select value={defaults.implement_model || ''}
          onChange={(e) => onSave({ implement_model: e.target.value || null })}>
          <option value="">CLI default</option>
          {FB_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
      <label>effort
        <select value={defaults.implement_effort || ''}
          onChange={(e) => onSave({ implement_effort: e.target.value || null })}>
          <option value="">default</option>
          {FB_EFFORTS.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
      <span className="muted">pre-fills the per-ticket selectors on approve</span>
    </div>
  )
}

function FbTicketEdit({ item, onSaved, onCancel }) {
  const t = item.ticket
  const [title, setTitle] = useState(t.title || '')
  const [summary, setSummary] = useState(t.summary || '')
  const [details, setDetails] = useState(t.details || '')
  const [area, setArea] = useState(t.area || 'other')
  const [acceptance, setAcceptance] = useState((t.acceptance || []).join('\n'))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const save = async () => {
    const acc = acceptance.split('\n').map((s) => s.trim()).filter(Boolean)
    if (!title.trim()) { setErr('title cannot be empty'); return }
    if (acc.length === 0) { setErr('add at least one acceptance criterion'); return }
    setBusy(true); setErr(null)
    try {
      await api.patchFeedbackTicket(item.id,
        { title: title.trim(), summary, details, area, acceptance: acc })
      onSaved()
    } catch (e) { setErr(String(e)) }
    setBusy(false)
  }

  return (
    <div className="fb-edit" onClick={(e) => e.stopPropagation()}>
      <label>title
        <input value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} /></label>
      <label>summary
        <textarea rows={2} value={summary} onChange={(e) => setSummary(e.target.value)} /></label>
      <label>details
        <textarea rows={5} value={details} onChange={(e) => setDetails(e.target.value)} /></label>
      <label>area
        <select value={area} onChange={(e) => setArea(e.target.value)}>
          {FB_AREAS.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </label>
      <label>acceptance criteria (one per line)
        <textarea rows={4} value={acceptance} onChange={(e) => setAcceptance(e.target.value)} /></label>
      <div className="fb-compose-row">
        <span className="fb-spacer" />
        {err && <span className="err inline">{err}</span>}
        <button className="ghost" disabled={busy} onClick={onCancel}>Cancel</button>
        <button disabled={busy} onClick={save}>{busy ? 'saving…' : 'Save ticket'}</button>
      </div>
    </div>
  )
}

function FeedbackItem({ item, onChanged, onErr, preview, defaults }) {
  const [open, setOpen] = useState(false)
  const [showLog, setShowLog] = useState(false)
  const [acting, setActing] = useState(false)
  const [composing, setComposing] = useState(false)
  const [editing, setEditing] = useState(false)
  // per-ticket overrides: undefined -> follow the global default live
  const [model, setModel] = useState(undefined)
  const [effort, setEffort] = useState(undefined)
  const [label, kind] = FB_CHIP[item.status] || [item.status, 'off']
  const t = item.ticket
  const busy = FB_BUSY.includes(item.status)
  const canComment = ['implemented', 'triaged'].includes(item.status)
  const canEdit = !busy && !!t && FB_EDITABLE.includes(item.status)
  const effModel = model !== undefined ? model : (defaults?.implement_model || '')
  const effEffort = effort !== undefined ? effort : (defaults?.implement_effort || '')
  const devUrl = item.status === 'implemented' && item.dev
    ? `${window.location.protocol}//${window.location.hostname}:${item.dev.port}/` : null
  const conflicted = item.merge_probe && !item.merge_probe.clean

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
        {preview && ['triaged', 'implemented', 'failed'].includes(item.status) &&
          <a className="muted" href={`${location.protocol}//${location.hostname}:8090/`}>
            testing copy — approve / accept / re-implement from the main dashboard ⧉</a>}
        {!preview && <>
          {item.status === 'triaged' && <>
            <button disabled={acting} onClick={() => act(null, () =>
              api.feedbackAction(item.id, 'approve', {
                implement_model: effModel || null,
                implement_effort: effEffort || null,
              }))}>✓ Approve — implement it</button>
            <label className="fb-implopt" title="model for this ticket's implementation pass">model
              <select value={effModel} disabled={acting} onChange={(e) => setModel(e.target.value)}>
                <option value="">default</option>
                {FB_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="fb-implopt" title="effort for this ticket's implementation pass (maps to a longer timeout server-side)">effort
              <select value={effEffort} disabled={acting} onChange={(e) => setEffort(e.target.value)}>
                <option value="">default</option>
                {FB_EFFORTS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <button className="ghost" disabled={acting} onClick={() => act('reject')}>Reject</button>
          </>}
          {devUrl && <>
            <a className="fb-preview" href={devUrl} target="_blank" rel="noreferrer">⧉ Open dev preview :{item.dev.port}</a>
            {conflicted && <>
              <span className="fb-conflict" title={`other accepted tickets changed: ${item.merge_probe.conflicts.join(', ')}`}>
                ⚠ conflicts with master ({item.merge_probe.conflicts.length})</span>
              <button disabled={acting} onClick={() => act('resolve')}
                title="Claude merges current master into this ticket's branch and resolves the conflicts there — master is untouched. Re-runs tests, rebuilds, restarts the preview for you to re-test. Much cheaper than re-implementing.">
                🤝 Resolve with Claude</button>
            </>}
            <button disabled={acting || conflicted} onClick={() => act('accept')}
              title={conflicted ? 'Resolve the conflicts with master first' : undefined}>✓ Accept & deploy</button>
            {item.comments?.length > 0 &&
              <button className="ghost" disabled={acting} onClick={() => act('retry')}
                title="Re-runs the coding pass on this ticket with all comments folded in (voice comments are transcribed first). Replaces this preview build.">
                ↻ Re-implement with comments</button>}
            <button className="ghost danger" disabled={acting} onClick={() => act('discard')}>Discard</button>
          </>}
          {item.status === 'failed' && <>
            {conflicted && item.worktree &&
              <button disabled={acting} onClick={() => act('resolve')}
                title="Claude merges current master into this ticket's branch and resolves the conflicts there — master is untouched. Keeps the existing implementation instead of redoing it.">
                🤝 Resolve conflicts with Claude</button>}
            <button disabled={acting} onClick={() => act('retry')}
              title={item.ticket
                ? 'The ticket already exists, so this re-runs only the implementation: fresh coding pass on the ticket + all comments (voice comments transcribed first). Triage is not redone.'
                : 'No ticket yet, so this re-runs intake: transcribe the memo and triage it into a ticket.'}>
              ↻ {item.ticket ? 'Retry implementation' : 'Retry transcription & triage'}</button>
            <button className="ghost" disabled={acting} onClick={() => act('reject')}>Reject</button>
          </>}
          {['done', 'rejected', 'discarded'].includes(item.status) &&
            <button className="ghost danger" disabled={acting}
              onClick={() => act(null, () => api.deleteFeedback(item.id))}>✕ remove</button>}
        </>}
        {canEdit &&
          <button className="ghost" onClick={() => setEditing((s) => !s)}>
            {editing ? 'cancel edit' : '✎ edit ticket'}</button>}
        {canComment &&
          <button className="ghost" onClick={() => setComposing((s) => !s)}>
            {composing ? 'hide comment' : '💬 comment'}</button>}
        {(item.status === 'implementing' || item.status === 'resolving' || item.status === 'accepting' || item.impl) &&
          <button className="ghost" onClick={() => setShowLog((s) => !s)}>
            {showLog ? 'hide log' : 'show log'}</button>}
      </div>

      {editing && canEdit && <FbTicketEdit item={item}
        onSaved={() => { setEditing(false); onChanged() }}
        onCancel={() => setEditing(false)} />}

      {showLog && <FbLog id={item.id} live={busy} />}

      {(item.comments?.length > 0 || composing) && (
        <div className="fb-comments" onClick={(e) => e.stopPropagation()}>
          {item.comments?.map((c, i) => (
            <div key={i} className="fb-comment">
              <span className="fb-comment-when">{c.at.slice(5, 16).replace('T', ' ')}</span>
              {c.source === 'voice' && <audio controls preload="none" src={`/api/feedback/${item.id}/comment/${i}/audio`} />}
              {c.text
                ? <span className="fb-comment-text">{c.text}</span>
                : c.source === 'voice' && <span className="muted">transcribing…</span>}
            </div>
          ))}
          {composing && <FbCommentComposer id={item.id}
            onSubmitted={() => { setComposing(false); onChanged() }} />}
        </div>
      )}

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
  const [preview, setPreview] = useState(false)
  const [settings, setSettings] = useState({})

  const load = () => api.feedback().then((r) => { setItems(r.items); setErr(null) })
    .catch((e) => setErr(String(e)))
  useEffect(() => {
    api.health().then((h) => setPreview(!!h.dev_preview)).catch(() => {})
    api.settings().then(setSettings).catch(() => {})
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [])

  const saveDefaults = (partial) =>
    api.saveSettings(partial).then(setSettings).catch((e) => setErr(String(e)))

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
      {preview && <p className="fb-previewbanner">⚠ Dev preview — test the change and leave
        comments here; approve / accept / re-implement / discard happen on
        the <a href={`${location.protocol}//${location.hostname}:8090/`}>main dashboard</a>.</p>}
      <FeedbackComposer onSubmitted={load} recorder={recorder} />
      {!preview && <ImplDefaults defaults={settings} onSave={saveDefaults} />}
      {err && <p className="err">{err}</p>}
      {items.length === 0 && <p className="muted">Queue is empty — say what you wish this app did better.</p>}
      {groups.map(([name, list, cls]) => list.length > 0 && (
        <div key={name} className={`fb-group ${cls}`}>
          <h4>{name} <small>{list.length}</small></h4>
          {list.map((i) => <FeedbackItem key={i.id} item={i} onChanged={load} onErr={setErr}
            preview={preview} defaults={settings} />)}
        </div>
      ))}
      {archive.length > 0 && (
        <div className="fb-group off">
          <h4 className="fb-archtoggle" onClick={() => setShowArchive((s) => !s)}>
            {showArchive ? '▾' : '▸'} Archive <small>{archive.length}</small></h4>
          {showArchive && archive.map((i) =>
            <FeedbackItem key={i.id} item={i} onChanged={load} onErr={setErr}
              preview={preview} defaults={settings} />)}
        </div>
      )}
    </section>
  )
}

// ---------------- Training metrics tab ----------------

const PROBE_LABELS = {
  pure_pregame: 'Pregame AUC', duration_cond: 'Duration', items_cond: 'Items',
  outcome_cond: 'Outcome', kills_pair_probe: 'Kills pair', gpm_probe: 'GPM', hd_probe: 'Hero dmg',
}
const shortVer = (v) => (v ? v.replace(/^[a-z]+-/, '') : '')

function PrequentialTip({ active, payload }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="curve-tip">
      <b>{p.cycle}</b> · AUC {p.eval_auc?.toFixed(4)}<br />
      <span className="muted">live {p.version} · {p.n_days}d window</span>
    </div>
  )
}

function TrainingTab() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [sel, setSel] = useState(null)   // selected run version for the probe panel

  useEffect(() => { api.trainingHistory().then(setData).catch((e) => setErr(String(e))) }, [])

  const pq = data?.prequential || []
  const runs = useMemo(() => [...(data?.runs || [])].reverse(), [data])   // newest first
  const promoSet = useMemo(() => new Set((data?.promotions || []).map((p) => p.version)), [data])
  const selRun = runs.find((r) => r.version === sel)
    || runs.find((r) => r.version === data?.live) || runs[0]

  // snap each promotion onto an actual prequential x value (categorical axis needs an exact tick)
  const promoLines = useMemo(() => {
    const cycles = pq.map((p) => p.cycle)
    const seen = new Set()
    return (data?.promotions || []).map((p) => {
      const x = cycles.find((c) => c >= p.date)
      if (!x || seen.has(x)) return null
      seen.add(x)
      return { x, version: p.version }
    }).filter(Boolean)
  }, [data, pq])

  const yDomain = useMemo(() => {
    const a = pq.map((p) => p.eval_auc).filter((v) => v != null)
    if (!a.length) return [0.5, 0.7]
    const lo = Math.min(...a), hi = Math.max(...a), pad = (hi - lo) * 0.18 || 0.01
    return [lo - pad, hi + pad]
  }, [pq])

  if (err) return <p className="err">{err}</p>
  if (!data) return <p>Loading training history…</p>

  const onDisk = runs.filter((r) => r.on_disk).length
  const fromGit = runs.length - onDisk

  return (
    <section className="training">
      <div className="disco-head">
        <div>
          <h2>Training metrics &amp; model progression</h2>
          <p className="sub">Prequential health — the live model's lag-free AUC on the freshest unseen
            window — across {pq.length} nightly cycles, plus validation metrics for every model version
            ever trained: <b>{runs.length} runs</b> ({onDisk} on disk{fromGit ? `, ${fromGit} recovered from git history` : ''}).</p>
        </div>
      </div>

      <Card title="Prequential AUC over time"
        sub="each point = one nightly cycle scoring the live model on new, unseen days (▲ = a promotion)">
        {pq.length ? (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={pq} margin={{ top: 14, right: 18, bottom: 4, left: -8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a3340" />
              <XAxis dataKey="cycle" tick={{ fontSize: 11 }} minTickGap={26} />
              <YAxis domain={yDomain} tick={{ fontSize: 11 }} tickFormatter={(v) => v.toFixed(3)} />
              <Tooltip content={<PrequentialTip />} cursor={{ stroke: '#2a3340' }} />
              {promoLines.map((p) => (
                <ReferenceLine key={p.x} x={p.x} stroke="#16a34a" strokeDasharray="4 3"
                  label={{ value: `▲ ${shortVer(p.version)}`, fontSize: 9, fill: '#16a34a', position: 'top' }} />
              ))}
              <Line type="monotone" dataKey="eval_auc" stroke="#3b82f6" strokeWidth={2}
                dot={{ r: 2 }} activeDot={{ r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        ) : <p className="muted">No prequential log yet — it fills in as nightly cycles run.</p>}
      </Card>

      <div className="train-grid">
        <Card title="Model versions" sub={`${runs.length} runs · newest first · click to inspect probes`}>
          <div className="train-runs-wrap">
            <table className="combos train-runs">
              <thead><tr>
                <th>Version</th><th>Kind</th><th>Val AUC</th><th>Ep</th><th>Train ≤</th><th>From</th>
              </tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.version}
                    className={`${r.version === data.live ? 'is-live' : ''} ${r.version === selRun?.version ? 'sel' : ''}`}
                    onClick={() => setSel(r.version)}>
                    <td className="combo">
                      <span className="tv-name">{r.version}</span>
                      {r.version === data.live && <span className="badge live">live</span>}
                      {promoSet.has(r.version) && r.version !== data.live &&
                        <span className="badge promo" title="was promoted to live">▲</span>}
                      {!r.on_disk && <span className="badge git" title="pruned from disk — recovered from git history">git</span>}
                    </td>
                    <td className="tk">{r.kind}</td>
                    <td className="stat">{r.val_auc != null ? r.val_auc.toFixed(4) : '—'}</td>
                    <td className="stat">{r.epochs ?? '—'}</td>
                    <td className="stat">{r.train_cutoff ?? '—'}</td>
                    <td className="stat parent">{r.parent ? shortVer(r.parent) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title={selRun ? `Probes · ${selRun.version}` : 'Probes'}
          sub="held-out diagnostic heads for the selected run">
          {selRun ? (
            <>
              <div className="probe-headline">
                <span>Validation AUC</span>
                <b>{selRun.val_auc != null ? selRun.val_auc.toFixed(4) : '—'}</b>
              </div>
              <div className="probe-grid">
                {Object.entries(PROBE_LABELS).map(([k, label]) => {
                  const v = selRun.probes?.[k]
                  if (v == null) return null
                  return (
                    <div key={k} className="probe-cell">
                      <span className="probe-k">{label}</span>
                      <b>{v.toFixed(3)}</b>
                    </div>
                  )
                })}
              </div>
              {!Object.keys(selRun.probes || {}).length &&
                <p className="muted">No probe metrics recorded for this run.</p>}
            </>
          ) : <p className="muted">Select a run to see its probe metrics.</p>}
        </Card>
      </div>
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
          <button className={tab === 'training' ? 'on' : ''} onClick={() => setTab('training')}>Training</button>
          <button className={tab === 'feedback' ? 'on' : ''} onClick={() => setTab('feedback')}>
            Feedback{rec && <span className="rec-live-dot" title="recording in progress" />}
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
      {tab === 'discover' && <DiscoverTab onAdd={addCombo} meta={meta} nHeroes={model?.n_heroes} />}
      {tab === 'shots' && <ShotsTab onReview={reviewShot}
        heroById={Object.fromEntries(meta.heroes.map((h) => [h.id, h]))} />}
      {tab === 'training' && <TrainingTab />}
      {/* Feedback stays mounted (just hidden) so the composer survives tab switches */}
      <div style={tab === 'feedback' ? undefined : { display: 'none' }}>
        <FeedbackTab recorder={recorder} />
      </div>
    </div>
  )
}
