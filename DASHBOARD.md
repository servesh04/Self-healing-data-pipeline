# DASHBOARD.md — Analytics Dashboard Spec

> **For Claude Code:** This is a **presentation-layer change only.** Read this fully before
> writing code.
>
> **HARD BOUNDARY — do not cross:**
> - Do **not** modify `backend/graph/**` — nodes, routing, state, or build.
> - Do **not** modify `backend/pipeline/**` or any prompt.
> - Do **not** change how `deriveVisited` or `PatchDiff` source their data. Both were fixed
>   in Phase 5 to read from `history` (append-only) rather than live state fields. That fix
>   must survive this work untouched.
> - New backend code is **read-only aggregation queries** and new GET endpoints. Nothing else.
>
> Everything below is additive. If a task seems to require editing the graph, stop and ask.

---

## Why this work exists

The current landing page is a centered column containing a dropdown and "no runs yet." The
palette and typography are already correct — warm graphite, amber signal, serif display. The
problem is **absence of data**, not styling.

Meanwhile the system already stores: every run, every heal attempt with its specialist and
confidence, every assertion result, and a full append-only mapping patch history. None of it
is surfaced.

**Goal:** an overview page that shows the system's behaviour at rest, so the product reads as
an operational tool rather than a trigger form.

---

## STEP 0 — Seed the database first (do this before any UI work)

An empty dashboard looks broken regardless of design quality. Every layout decision below
depends on seeing real rows.

Run all six datasets multiple times to build history:

```
day1_clean      × 3     → clean, no healing
day2_renamed    × 3     → auto-healed, 1 attempt, high confidence
day3_type_drift × 3     → auto-healed, 1 attempt, high confidence
day4_combo      × 4     → auto-healed, 2 attempts  ← the cycle
day5_unfixable  × 3     → escalated, unknown, low confidence
day6_ambiguous  × 4     → 2 approved, 2 rejected   ← both HITL paths
```

Target ~20 runs with a realistic spread of outcomes. Use isolated `pipeline_env` namespaces
per the Phase 1 convention so seeding does not corrupt the live default mapping.

**Definition of Done:** `GET /api/runs` returns ≥20 runs covering all five terminal states
(clean, healed-auto, healed-approved, escalated-rejected, escalated-cap).

---

## New Backend Endpoints (read-only)

All aggregation happens in SQL. No new tables. No writes.

### `GET /api/analytics/summary`
```json
{
  "total_runs": 20,
  "drifted_runs": 17,
  "auto_healed": 10,
  "human_approved": 2,
  "escalated": 5,
  "auto_heal_rate": 0.588,
  "mean_heal_attempts": 1.4,
  "clean_runs": 3
}
```
`auto_heal_rate = auto_healed / drifted_runs` — **exclude clean runs from the denominator.**
A clean run never needed healing; counting it inflates the number and someone will ask.

### `GET /api/analytics/confidence`
One row per heal attempt — this drives the calibration chart:
```json
[
  { "run_id": "...", "attempt_no": 1, "confidence": 0.94,
    "drift_class": "rename", "specialist": "spec_rename",
    "result": "passed", "approved_by": "auto" }
]
```

### `GET /api/analytics/drift_distribution`
```json
[ { "drift_class": "rename", "count": 9, "healed": 8, "escalated": 1 } ]
```

### `GET /api/analytics/specialist_performance`
```json
[ { "specialist": "spec_rename", "attempts": 9, "passed": 8, "success_rate": 0.889 } ]
```

### `GET /api/runs?limit=&status=&dataset=`
Extend the existing endpoint with optional filters. Return a **lean projection** for table
rows — do not return full `HealState` per row:
```json
{ "run_id": "...", "dataset": "day4_combo.csv", "status": "healed",
  "drift_class": "rename", "heal_attempts": 2, "final_confidence": 0.91,
  "duration_ms": 8420, "approved_by": "auto", "created_at": "..." }
```

---

## Layout

### App shell (wraps every page)

```
┌────────────────────────────────────────────────────────────────────────┐
│ ◆ SELF-HEALING PIPELINE          [env: render]        [ + New run ]    │  56px
├────────────────────────────────────────────────────────────────────────┤
│  Overview   Runs   Mapping                                             │  40px
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│                          page content                                  │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

- **Full width**, `max-width: 1600px`, `padding: 0 32px`. The current 1200px centered column
  is the main reason the page reads as empty.
- Mark: a simple amber glyph (`◆` or a 16px SVG). No logo design work.
- Environment chip: muted, monospace, tiny.

### Overview page

```
┌──────────┬──────────┬──────────┬──────────┐
│ RUNS     │ AUTO-HEAL│ MEAN     │ ESCALAT. │   KPI strip, 4 across
│ 20       │ 59%      │ 1.4      │ 5        │   112px tall
│ 17 drift │ 10 of 17 │ attempts │ 3 unknown│
└──────────┴──────────┴──────────┴──────────┘

┌─────────────────────────────────┬──────────────────────┐
│ RECENT RUNS                     │ CONFIDENCE           │
│ ┌─────────────────────────────┐ │ CALIBRATION          │
│ │ dense table, ~14 rows       │ │                      │
│ │ 36px row height             │ │  scatter plot        │
│ │                             │ │                      │
│ └─────────────────────────────┘ │                      │
│                                 ├──────────────────────┤
│                                 │ DRIFT CLASSES        │
│                                 │  stacked bars        │
└─────────────────────────────────┴──────────────────────┘
        2fr                              1fr

┌─────────────────────────────────────────────────────────┐
│ SPECIALIST PERFORMANCE      (horizontal bars, 3 rows)   │
└─────────────────────────────────────────────────────────┘
```

---

## Components

### 1. KPI strip
Four cards. Large figure in the display serif (32px), label above in 11px uppercase mono with
`0.08em` tracking, sub-line below in `--text-faint`.

**No sparklines, no trend arrows.** Twenty runs over one day is not a time series and faking
one is worse than omitting it.

### 2. Runs table — *the highest-value component here*

A dense, real table is the single strongest "this is a product" signal. Prioritise it over
every chart.

| Column | Notes |
|---|---|
| Status | chip — see colour rules below |
| Dataset | mono, `--text` |
| Drift | mono, `--text-muted`; `—` when clean |
| Attempts | right-aligned numeral; **amber when ≥2** |
| Confidence | right-aligned, 2dp, `--text-muted` |
| Approved by | `auto` / `human` / `—`, `--text-faint` |
| Duration | right-aligned, `8.4s` |
| Time | right-aligned, `--text-faint` |

Row height 36px, `border-bottom: 1px solid --border`, hover `--surface-raised`, whole row
links to run detail. Header row 11px uppercase mono, `--text-faint`, sticky.

Filter chips above the table: `All / Healed / Escalated / Awaiting / Clean`.

### 3. Confidence calibration — *the most distinctive chart in the project*

Scatter: **x = confidence (0–1)**, **y = attempt number (jittered)**, one point per heal
attempt.

- Colour by outcome: `--pass` passed, `--fail` failed, `--signal` awaiting/human-approved
- Vertical dashed amber line at `x = 0.75` labelled `CONFIDENCE_THRESHOLD`
- Points left of the line should cluster in escalate/human outcomes; right of it, in passes

This visualises exactly what Phase 3 tuned — that the threshold actually separates the cases.
It is evidence, not decoration. Almost no student project has a chart that proves its own
calibration works. **Do not cut this before the drift distribution.**

### 4. Drift distribution
Horizontal stacked bars, one row per class (`rename`, `type`, `nullability`, `unknown`),
segmented healed (`--pass`) vs escalated (`--fail`). Count at the right in mono.

### 5. Specialist performance
Horizontal bars: `spec_rename`, `spec_type`, `spec_nullability` with success rate and
`8/9` style counts. Show a specialist with zero attempts as an empty row labelled `no data`
rather than hiding it — an absent row looks like a bug.

### 6. Mapping timeline — *build last, cut first*
The mapping table is append-only, so patches accreted over time are already queryable.
Vertical timeline, one entry per mapping version: timestamp, run link, and the patch as a
`+`/`-` diff. Lands well with anyone who thinks about data lineage, but it is the least
load-bearing item here.

---

## Design Tokens

Extend the existing token file. **Do not redefine what is already there** — the palette and
typography are correct and were approved.

```css
--pass:    #6E9182;   /* healed / assertion pass */
--fail:    #C05E4A;   /* escalated / assertion fail */
--signal:  #E0A458;   /* heal cycle, human attention, threshold line */
--running: #7A93A8;
--idle:    #3A3733;
```

**Status chip mapping — one colour per meaning, no exceptions:**

| Status | Colour | Label |
|---|---|---|
| clean | `--text-muted` | `CLEAN` |
| healed (auto) | `--pass` | `HEALED` |
| healed (human) | `--pass` w/ amber left border | `HEALED · APPROVED` |
| escalated | `--fail` | `ESCALATED` |
| awaiting_approval | `--signal`, pulsing | `AWAITING` |
| running | `--running` | `RUNNING` |

**Colour scarcity rule.** Chart axes, gridlines, table borders, and inactive chips are all
neutral. Saturation appears only where it carries meaning. The `↺ Heal attempt N` badge on
run detail remains the most saturated element in the entire application — nothing on the
overview page may exceed it.

**Numerals:** enable `font-variant-numeric: tabular-nums` on every numeric column and KPI, or
figures will jitter as they poll.

---

## Charts

Use **Recharts** — already listed as available, and hand-rolling scatter and bar charts on the
final day is a poor trade. The graph canvas was worth hand-rolling; these are not.

Configure every chart to match the palette explicitly: `--border` gridlines at low opacity,
`--text-faint` axis labels at 11px mono, no default legend styling, no drop shadows, no
gradients. Recharts' defaults are generic and will fight the aesthetic if left alone.

---

## Run Detail — minimal changes

Keep all existing logic. Two layout changes only:

1. Wrap in the app shell with a breadcrumb: `Overview / Runs / a1093...`
2. Move `AssertionBars`, `SchemaDiff`, `PatchDiff`, `ApprovalPanel` from stacked-below into a
   **380px right sidebar**, letting `GraphCanvas` take the remaining width.

Add the per-run mapping audit trail (added in Phase 5) as a collapsed section at the bottom of
the sidebar.

**Do not touch** `deriveVisited`, `PatchDiff`'s `history` sourcing, or the polling logic.

---

## Build Order

Cut from the bottom. Each step is independently shippable.

| # | Item | Why this order |
|---|---|---|
| 1 | Seed the database | Everything else depends on real data existing |
| 2 | App shell, full width | Largest perceived improvement, lowest cost |
| 3 | Analytics endpoints | Unblocks 4–7 |
| 4 | KPI strip | High signal, trivial to build |
| 5 | **Runs table** | The strongest "this is a product" component |
| 6 | **Confidence calibration** | The most distinctive chart; evidence, not decoration |
| 7 | Drift distribution | Nice to have |
| 8 | Specialist performance | Nice to have |
| 9 | Run detail sidebar | Layout only |
| 10 | Mapping timeline | **Cut first if time runs short** |

---

## Verification (mirror the Phase 5 approach)

Phase 5 found two real bugs only by rendering live backend data and looking at screenshots.
Same discipline here:

- [ ] Every KPI cross-checked against a direct SQL query — not trusted from the endpoint alone
- [ ] `auto_heal_rate` denominator **excludes clean runs**
- [ ] Runs table filters produce correct row counts for all five statuses
- [ ] Confidence scatter: `day5` points sit left of the 0.75 line, `day2`/`day3` right of it.
      **If they don't, the chart is wrong — or the model was swapped and calibration drifted.**
- [ ] A specialist with zero attempts renders as `no data`, not an absent row
- [ ] Empty state (fresh `pipeline_env`) renders deliberately, not as a broken layout
- [ ] Run detail still works end to end; `↺ Heal attempt 2` still fires on `day4_combo`
- [ ] Screenshot-verified against the **deployed** backend, not only local

---

## Model Switch Warning

If the agent's LLM is being switched from `gpt-oss-120b` to an OpenAI model, **the Phase 3
calibration does not carry over.** The 25/25 sweep, `day5` → 0.2, `day6` → 0.3, and
`CONFIDENCE_THRESHOLD = 0.75` were tuned against one specific model's confidence distribution.
GPT-4o-class models express confidence differently — `day6` could return 0.8 and stop
escalating, which silently destroys the human-approval demo path.

If the model changes: re-run the calibration sweep **before** seeding the database, or the
dashboard will faithfully visualise a broken threshold.

Also note the gpt-oss-120b list-wrapping normalizer may be unnecessary — or actively wrong —
against a different provider. Check it.

---

*End of Dashboard Spec*
