// The 6 hand-authored fault datasets (data/sources/) — shared by the "+ New
// run" trigger and the Runs page's dataset filter.
export const DATASETS = [
  { file: 'day1_clean.csv', label: 'day1 — clean (control)' },
  { file: 'day2_renamed.csv', label: 'day2 — renamed column' },
  { file: 'day3_type_drift.csv', label: 'day3 — type drift' },
  { file: 'day4_combo.csv', label: 'day4 — combo (the cycle)' },
  { file: 'day5_unfixable.csv', label: 'day5 — unfixable (escalates)' },
  { file: 'day6_ambiguous_rename.csv', label: 'day6 — ambiguous (human review)' },
]
