# App Shell, Routing & Prototype Authentication — Proposal

**Agent:** Agent 1 — Application Shell and Authentication
**Worktree:** `C:\Stuff\shdp-shell` · **Branch:** `feature/app-shell`
**Status:** Phase A proposal (implementation follows; scope is pre-approved by the orchestrator)
**Scope:** routing, login, prototype session, protected routes, sidebar shell, workspace/profile page. **No backend changes.**

---

## 1. Design note (read first)

The project already has an approved, deliberately architectural design system: warm graphite ground, amber "signal", serif display (Fraunces), IBM Plex Mono for data, and a strict colour-scarcity rule (`DASHBOARD.md` §Design Tokens: *"Do not redefine what is already there — the palette and typography are correct and were approved"*).

The expansion brief also asks for a "light neutral background with restrained indigo/blue accents". Those two directions are mutually exclusive. This proposal **keeps the existing, approved system** and builds the new shell/login/workspace in it, because:

1. `DASHBOARD.md` explicitly freezes the palette and status-colour semantics.
2. Existing pages and charts read their colours from the same `--color-*` tokens; a re-skin is an app-wide change, not a shell change.
3. `GraphCanvas` renders the heal-attempt badge with `--color-canvas` text on an amber fill. In a light theme that becomes white-on-amber and fails contrast; `GraphCanvas` is outside this agent's ownership, so the shell cannot safely invert the theme alone.

**Recommendation to the orchestrator:** if a light/indigo re-skin is required, schedule it as a separate, app-wide design pass (it needs `index.css`, `GraphCanvas`, and chart-token verification) rather than folding it into the shell branch. Nothing in this implementation blocks such a pass: all new components use the same semantic tokens (`bg-canvas`, `bg-surface`, `border-line`, `text-ink[-muted|-faint]`, `text-signal`) and will follow a future token change automatically.

---

## 2. Endpoints

**None.** This agent adds no API surface:

- `POST /api/auth/login`, `GET /api/auth/me`, `PATCH /api/workspace/profile` from `API_CONTRACT.md` §3.1–3.2 are **not implemented** on the backend. The user brief for this agent explicitly forbids backend edits and requires frontend-only prototype authentication.
- The demo session token **is the existing `VITE_SHARED_TOKEN`**, so every existing `/api/*` call continues to authenticate exactly as before. No new token format, no new secrets.

### Demo account (frontend-only)

| Field | Value |
|---|---|
| Email | `demo@shdp.dev` |
| Password | `heal-demo` |
| User id | `usr_demo_001` |
| Name | `Dana Okafor` |
| Role | `Data Platform Engineer` |
| Workspace | `Analytics Platform — Demo` |

Credentials are hard-coded in `useAuthStore` and visibly labelled **"Prototype authentication — browser-only, not production security"** on the login page, in the sidebar account block, and on the workspace page. Any other email/password is rejected with an inline error.

---

## 3. Session storage (pinned contract)

`localStorage` key: **`shdp.session`**. Exact JSON:

```json
{
  "token": "<VITE_SHARED_TOKEN>",
  "user": {
    "id": "usr_demo_001",
    "name": "Dana Okafor",
    "email": "demo@shdp.dev",
    "role": "Data Platform Engineer",
    "workspace": "Analytics Platform — Demo"
  }
}
```

Behaviour:

- Written on successful demo login; read on boot; removed on logout and on a `401` from an authenticated API call.
- `api/client.js` resolves `Authorization` as: **session token first**, then `VITE_SHARED_TOKEN`, then no header. It also exports `request` for the other agents' API modules.
- Session restore is synchronous from `localStorage` (no flash of the login page on refresh).

---

## 4. Routes

| Path | Component | Protection |
|---|---|---|
| `/login` | `pages/LoginPage.jsx` (new) | Public; redirects to `/` when already signed in |
| `/` | `pages/Overview.jsx` (existing) | Protected |
| `/ingestion` | **local placeholder** (`components/Shell/Placeholders.jsx`) | Protected |
| `/contracts` | **local placeholder** (`components/Shell/Placeholders.jsx`) | Protected |
| `/runs` | `pages/RunsPage.jsx` (existing) | Protected |
| `/runs/:runId` | `pages/RunDetail.jsx` (existing) | Protected |
| `/mapping` | `pages/MappingPage.jsx` (existing) | Protected |
| `/workspace` | `pages/WorkspacePage.jsx` (new) | Protected |
| `*` | redirect to `/` | Protected |

`/runs/:runId` is a real URL now (deep-linkable, refreshes correctly) instead of an overlay driven by `selectedRunId`.

### Placeholder swap points (one line each)

`App.jsx` aliases the placeholders so the orchestrator only edits the import line:

```js
import { IngestPlaceholder as IngestPage } from './components/Shell/Placeholders'
import { ContractsPlaceholder as ContractsPage } from './components/Shell/Placeholders'
```

Integration replaces each line with:

```js
import IngestPage from './pages/IngestPage'
import ContractsPage from './pages/ContractsPage'
```

The `<Route element={<IngestPage />}>` entries do **not** change.

---

## 5. Navigation ↔ existing Zustand state

`useRunStore` keeps its polling, analytics, mapping, approvals, chat and fetch methods untouched. Only route synchronisation is added, in a shell component (`components/Shell/RunRouteSync.jsx`) — no store edits are required:

- run row click → `selectRun(id)` (existing behaviour) → shell navigates to `/runs/:id`;
- deep link / browser back/forward → shell calls `selectRun(id)` or `clearSelection()`;
- `RunDetail`'s back link calls `clearSelection()` (existing behaviour) → shell navigates to the stored `currentPage` (`/`, `/runs`, or `/mapping`);
- `currentPage` is mirrored from the URL for the three legacy pages so the run-detail breadcrumb stays correct;
- `trigger()` still selects the new run and now lands on its URL.

Guard against loops: URL→store runs only on pathname change; store→URL runs only when `selectedRunId` changes.

Every sign-out path (sidebar, top bar, workspace, and the API-401 handler) calls the existing `useRunStore.getState().clearSelection()` first, so the 2-second run poll stops instead of continuing behind the login screen.

---

## 6. Component / file plan

### New

| File | Purpose |
|---|---|
| `frontend/src/lib/session.js` | Storage key, demo account/user constants, read/write/clear, token mask (shared by store + client) |
| `frontend/src/store/useAuthStore.js` | Demo session, login/logout, localStorage persistence, profile-name update |
| `frontend/src/pages/LoginPage.jsx` | Split-panel login, demo-credential card, prototype warning |
| `frontend/src/pages/WorkspacePage.jsx` | Profile + workspace details, session/API status, display-name edit, logout |
| `frontend/src/components/Shell/AppShell.jsx` | Protected layout: sidebar + top bar + `<Outlet />` + `NewRunModal` |
| `frontend/src/components/Shell/Sidebar.jsx` | Persistent nav (desktop), slide-over nav (mobile), account block |
| `frontend/src/components/Shell/RequireAuth.jsx` | Route guard + `from` state for post-login return |
| `frontend/src/components/Shell/RunRouteSync.jsx` | URL ↔ `useRunStore` bridge described in §5 |
| `frontend/src/components/Shell/Placeholders.jsx` | `IngestPlaceholder` / `ContractsPlaceholder` |
| `frontend/src/components/Shell/icons.jsx` | Inline stroke icons used by the shell |
| `docs/app-proposals/app-shell.md` | This document |

### Modified

| File | Change |
|---|---|
| `frontend/src/main.jsx` | Wrap `<App />` in `<BrowserRouter>` |
| `frontend/src/App.jsx` | Route table + guards; no more `PAGES`/overlay switching |
| `frontend/src/components/Shell/TopBar.jsx` | Page title, mobile menu button, env chip, contract selector, New run, account menu/logout |
| `frontend/src/components/Shell/NewRunModal.jsx` | Token-based overlay/positioning only (behaviour unchanged) |
| `frontend/src/api/client.js` | Session-first token resolution; export `request`; 401 clears prototype session |
| `frontend/package.json`, `frontend/package-lock.json` | Add `react-router-dom` |

`frontend/src/components/Shell/NavTabs.jsx` is left on disk but is no longer rendered (superseded by the sidebar). No existing page under `frontend/src/pages/` is edited except via the route table; `ContractsPage.jsx` and `IngestPage.jsx` are **not** created.

---

## 7. Verification plan

### Build

```powershell
cd C:\Stuff\shdp-shell\frontend
npm install
npm run build
```

### Manual checklist

**Login / logout**

1. `/` while signed out → redirected to `/login`.
2. Wrong password → inline error, no session written.
3. Demo credentials → lands on Overview; `shdp.session` exists with the exact shape in §3.
4. Refresh on any protected URL → session restored, no login flash.
5. Logout from sidebar and from Workspace → `/login`, `shdp.session` removed, back button cannot re-enter.
6. Manually delete `shdp.session` and reload a protected URL → `/login`.

**Routing / sidebar**

7. Each of the six nav items reaches its exact path; active item is highlighted; unknown path redirects to `/`.
8. Direct load of `/runs`, `/mapping`, `/workspace` works (Vite SPA fallback).
9. Sidebar collapses to a drawer under `lg`; overlay closes on navigation and on Escape.

**Existing functionality**

10. Overview analytics, KPI strip, charts, specialist performance, component evidence and global chat still load.
11. Runs table filter chips work; row click opens `/runs/<id>`; browser Back returns to `/runs`.
12. Run detail: graph, assertions, schema diff, patch diff, approval panel, mapping audit and run chat render; polling continues for active runs and stops on back/terminal state.
13. New run modal still triggers and navigates to the created run.
14. Contract selector in the top bar still rescopes runs/analytics/mapping.
15. Mapping history and the seed-reset toggle still work.

**Auth plumbing**

16. All API calls carry `Authorization: Bearer <VITE_SHARED_TOKEN>` after login.
17. A `401` clears the session and returns to `/login` (test by setting a bad session token).
18. Login page, sidebar and Workspace all display the prototype-authentication warning.

### Automated

- `npm run build` must pass (JSX/import/route validation is the practical automated gate; the repo has no frontend test runner and none is added).

---

## 8. Shared-file changes requested

| File | Request | Owner action |
|---|---|---|
| `frontend/src/App.jsx` | Route table, placeholder aliases, protected layout | already owned by this agent (integration may swap 2 import lines) |
| `frontend/src/api/client.js` | session-first token, `request` export | already owned by this agent for auth-token handling; other agents may import `request` |
| `frontend/package.json` / lock | `react-router-dom` | this agent |
| `frontend/src/index.css` | **no change proposed** (see §1) | — |
| `backend/**` | **no change** | — |

---

## 9. Risks / open items for the orchestrator

1. **Design direction conflict** (§1) — needs a decision if light/indigo is mandatory.
2. **Frontend-only auth** is cosmetic: the shared bearer token is in the bundle, so the login screen gates UI, not the API. This is labelled prototype-only everywhere it appears.
3. **Deployment rewrites:** SPA deep links require the host to rewrite unknown paths to `index.html`. Vercel/Render config is outside this branch; flag for release.
4. **`currentPage` in `useRunStore`** is retained and mirrored, purely so the existing `RunDetail` breadcrumb keeps working without editing that page.
