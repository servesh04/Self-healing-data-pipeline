/**
 * Small inline icon set for the application shell. Stroke-based, 20×20,
 * inheriting `currentColor` — no icon dependency and no decoration beyond what
 * navigation needs.
 */

function Icon({ children, className = 'h-4 w-4' }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {children}
    </svg>
  )
}

export function IconOverview(props) {
  return (
    <Icon {...props}>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </Icon>
  )
}

export function IconIngest(props) {
  return (
    <Icon {...props}>
      <path d="M12 16V4" />
      <path d="m7 9 5-5 5 5" />
      <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
    </Icon>
  )
}

export function IconContracts(props) {
  return (
    <Icon {...props}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6M9 17h4" />
    </Icon>
  )
}

export function IconRuns(props) {
  return (
    <Icon {...props}>
      <path d="M4 6h10M4 12h16M4 18h7" />
      <circle cx="18" cy="6" r="2" />
      <circle cx="15" cy="18" r="2" />
    </Icon>
  )
}

export function IconMapping(props) {
  return (
    <Icon {...props}>
      <path d="M4 6h5M15 6h5M4 18h5M15 18h5" />
      <path d="M9 6c0 6 6 6 6 12" />
      <circle cx="9" cy="6" r="2" />
      <circle cx="15" cy="18" r="2" />
    </Icon>
  )
}

export function IconWorkspace(props) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </Icon>
  )
}

export function IconLogout(props) {
  return (
    <Icon {...props}>
      <path d="M14 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-3" />
      <path d="M10 12h10" />
      <path d="m17 9 3 3-3 3" />
    </Icon>
  )
}

export function IconMenu(props) {
  return (
    <Icon {...props}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </Icon>
  )
}

export function IconClose(props) {
  return (
    <Icon {...props}>
      <path d="m6 6 12 12M18 6 6 18" />
    </Icon>
  )
}

export function IconPlus(props) {
  return (
    <Icon {...props}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  )
}

export function IconArrowRight(props) {
  return (
    <Icon {...props}>
      <path d="M5 12h14" />
      <path d="m13 6 6 6-6 6" />
    </Icon>
  )
}

export function IconShield(props) {
  return (
    <Icon {...props}>
      <path d="M12 3 5 6v5c0 4.5 3 7.7 7 10 4-2.3 7-5.5 7-10V6z" />
      <path d="m9 12 2 2 4-4" />
    </Icon>
  )
}
