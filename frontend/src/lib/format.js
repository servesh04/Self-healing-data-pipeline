export function formatDuration(ms) {
  if (ms == null) return '—'
  return `${(ms / 1000).toFixed(1)}s`
}

export function formatConfidence(c) {
  return c == null ? '—' : c.toFixed(2)
}

export function formatTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString() : '—'
}
