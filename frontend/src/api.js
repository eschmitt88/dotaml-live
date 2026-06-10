// Thin fetch wrapper for the dotaml-live JSON API.
async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${path} -> ${r.status} ${await r.text()}`)
  return r.json()
}
async function get(path) {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${path} -> ${r.status}`)
  return r.json()
}

async function postBlob(path, blob) {
  const r = await fetch(path, { method: 'POST', body: blob })
  if (!r.ok) throw new Error(`${path} -> ${r.status} ${await r.text()}`)
  return r.json()
}

async function del(path) {
  const r = await fetch(path, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${path} -> ${r.status}`)
  return r.json()
}

export const api = {
  meta: () => get('/meta'),
  draftFromScreenshot: (blob) => postBlob('/api/draft-from-screenshot', blob),
  screenshots: (status = 'all') => get(`/api/screenshots?status=${status}`),
  labelScreenshot: (id, b) => post(`/api/screenshots/${id}/label`, b),
  deleteScreenshot: (id) => del(`/api/screenshots/${id}`),
  model: () => get('/model'),
  winprob: (b) => post('/api/winprob', b),
  heroPicks: (b) => post('/api/hero-picks', b),
  winVsDuration: (b) => post('/api/win-vs-duration', b),
  itemBuild: (b) => post('/api/item-build', b),
  heroCombos: (b) => post('/api/hero-combos', b),
  combosTable: () => get('/api/combos-table'),
  patchStatus: () => get('/api/patch-status'),
}
