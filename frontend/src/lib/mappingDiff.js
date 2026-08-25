// mapping_state rows are full snapshots, not fragments (services/store.py:
// "each row is a full mapping snapshot") — so a per-entry +/- diff for the
// timeline has to be computed here, against the previous snapshot.
export function diffMappings(prev, curr) {
  const lines = []
  for (const section of ['renames', 'casts', 'null_policy']) {
    const prevObj = prev?.[section] || {}
    const currObj = curr?.[section] || {}
    for (const [k, v] of Object.entries(currObj)) {
      if (prevObj[k] !== v) lines.push({ sign: '+', text: `${section}: ${k} → ${v}` })
    }
    for (const [k, v] of Object.entries(prevObj)) {
      if (!(k in currObj)) lines.push({ sign: '-', text: `${section}: ${k} → ${v}` })
    }
  }
  const prevDrops = new Set(prev?.drops || [])
  const currDrops = new Set(curr?.drops || [])
  for (const d of currDrops) if (!prevDrops.has(d)) lines.push({ sign: '+', text: `drops: ${d}` })
  for (const d of prevDrops) if (!currDrops.has(d)) lines.push({ sign: '-', text: `drops: ${d}` })
  return lines
}
