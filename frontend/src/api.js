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

export const api = {
  meta: () => get('/meta'),
  model: () => get('/model'),
  winprob: (b) => post('/api/winprob', b),
  heroPicks: (b) => post('/api/hero-picks', b),
  winVsDuration: (b) => post('/api/win-vs-duration', b),
  itemBuild: (b) => post('/api/item-build', b),
  heroCombos: (b) => post('/api/hero-combos', b),
  combosTable: () => get('/api/combos-table'),
  patchStatus: () => get('/api/patch-status'),
}
