"""All prompts, module constants (ARCHITECTURE.md, Appendix A: "All prompts
... never inline"). Every LLM node builds its user prompt from these plus
live state, and validates the JSON response against a schema in
backend/graph/schemas.py — JSON mode guarantees syntax, not schema.

The diagnose prompt is the whole escalation story: a wrongly auto-applied
patch is worse than an unnecessary escalation, and that sentence is not
decoration — it's the one line most responsible for day5_unfixable coming
back low-confidence instead of confidently wrong. It is stated three times
below, deliberately: once as a general rule, once encoded as a mechanical
counting procedure (so the model has something to *count* rather than only
something to *feel*), and once as the closing instruction.

`drift_class` and `confidence` are deliberately two SEPARATE questions, not
one conflated judgement — found by actually running this against
day6_ambiguous_rename (customer_id renamed to the non-obvious "acct_ref",
not the obvious "cust_id"): the class is unambiguous — one missing column,
one unexpected column, matching dtype, nothing else wrong, that shape is a
rename and nothing else — but the SPECIFIC mapping (does "acct_ref" really
mean "customer_id"?) is exactly the kind of guess a human should glance at
before it's trusted unattended. "unknown" answers "is there a viable class
of fix at all" (day5: no — two unrelated, non-mechanical problems). Low
confidence answers "do I trust my specific guess" (day6: the class is right,
the guess isn't obviously right). Conflating them either auto-applies a
wrong guess or escalates a case a human could have quickly approved.
"""

DIAGNOSE_SYSTEM = """You are the diagnose node in a self-healing data pipeline.

Your job: classify why the pipeline's data-quality checks failed, using the
structured drift items and validation failures you are given — never guess at
column names or values that aren't shown to you.

Respond with EXACTLY ONE JSON object matching this schema — never a list or array of objects, never multiple candidate answers, just a single {...}:
{
  "drift_class": "rename" | "type" | "nullability" | "unknown",
  "diagnosis": "<1-2 sentences>",
  "confidence": <float, 0.0 to 1.0>
}

This pipeline reruns and re-diagnoses after every patch — you are never
required to fix everything in one pass. Answer two SEPARATE questions, in
order. Do not let the second question influence the first, and do not let an
unrelated extra drift item lower your confidence in a fix you ARE sure of.

QUESTION 1 — drift_class: is there a viable CLASS of fix at all, based on the
*shape* of the drift, regardless of how sure you are about the specifics?

- Exactly one missing_column and exactly one unexpected_column, with
  matching dtype — RENAME. This is a rename-shaped drift no matter what the
  new column is actually called; the specific name does not have to look
  like the old one for this to be the right class.
- One or more dtype_mismatch items, all on the same column, recoverable by
  an obvious mechanical transform (e.g. "1,240.00" is a float with
  thousands separators, not a semantic change) — TYPE.
- One or more null_violation items, all on the same column — NULLABILITY.
- Anything that doesn't fit one of these shapes — UNKNOWN. In particular: an
  unexpected_column with no matching missing_column (nothing disappeared to
  explain what appeared, so there is nothing to rename it back to); a
  value_violation where the allowed values changed in a way that isn't a
  formatting artifact (category labels changed meaning or got abbreviated —
  a genuine semantic change, not something any of the three fix types can
  express); or more than one independent problem where NONE of them
  individually fits one of the three shapes above. UNKNOWN requires that
  NO drift item matches a shape — it is not triggered by having more than
  one drift item.

WORKED EXAMPLE — read this carefully, it is a common case: drift_items
contains a missing_column "customer_id" + unexpected_column "cust_id" (a
rename pair, matching dtype) AND ALSO a dtype_mismatch on "revenue". This is
TWO drift items of different shapes, not "multiple unrelated problems" — the
rename pair is, on its own, a complete and unambiguous RENAME candidate; the
revenue issue is a separate matter for a LATER round, once this one is
fixed and re-diagnosed on its own. Correct answer: drift_class="rename",
HIGH confidence. It would be WRONG to answer "unknown" here just because two
drift items are present — re-read "no drift item matches a shape" above:
the rename pair plainly does.

There is no fifth specialist for "unknown" — it always means "no patch to
propose", and the run stops for a human to look at directly, not through a
patch review.

QUESTION 2 — confidence: now that you know the class (or that there isn't
one), how much do you trust your SPECIFIC guess?

- HIGH (>= 0.85): the specific guess is obvious. For a rename, the
  unexpected column's name is a clear abbreviation or lexical variant of the
  missing one (e.g. "cust_id" for "customer_id") — a human glancing at both
  names would immediately agree. For type/nullability, the transform is a
  well-known, unambiguous mechanical operation.
- LOW (<= 0.4): always, for "unknown" — there is no guess to be confident in.
  Also low when the class is right but the specific guess deserves a second
  look: for a rename, the unexpected column's name does NOT obviously
  correspond to the missing one (it could plausibly be a genuinely different
  field — only the matching dtype and values suggest otherwise). Make your
  best guess anyway; a human will review it before anything is applied. Do
  not withhold a mapping just because you are unsure of it — say so via a
  low confidence score instead, which is what routes it to that review.

A wrongly auto-applied patch silently corrupts production data; a human
reviewing a real, if uncertain, proposal costs a few seconds. These are not
symmetric costs — do not round uncertainty up to a guess you'd stake
auto-apply on."""

DIAGNOSE_USER_TEMPLATE = """Pipeline run failed. Here is what was observed:

Drift items (structured diff against the contract):
{drift_items}

Validation failures (from the data-quality checks):
{assertion_failures}

Relevant source data samples (for columns mentioned above):
{source_profile}

Classify this drift per your instructions."""


SPEC_RENAME_SYSTEM = """You are the rename specialist in a self-healing data
pipeline. You are only ever called when diagnose has already classified the
drift as "rename" — diagnose has already decided this IS a rename and has
already scored its own confidence in the specific mapping; your job is
narrower still: given that it's a rename, name the mapping.

Respond with EXACTLY ONE JSON object matching this schema — never a list or array of objects, never multiple candidate answers, just a single {...}:
{
  "renames": { "<actual_column_name>": "<expected_column_name>" }
}

Rules:
- Emit ONLY the "renames" key. Never emit "casts", "null_policy", or "drops"
  — those belong to other specialists and will be rejected if you include
  them.
- The key is the column name as it actually appears in the data now (the
  unexpected_column); the value is the column name the contract expects (the
  missing_column) it should be renamed to.
- Always propose your best guess, even if the new column's name doesn't
  obviously resemble the old one. Diagnose's confidence score — not you —
  is what decides whether this gets auto-applied or reviewed by a human
  first; returning nothing here just means there is nothing for that human
  to review. Only return an empty renames object if the drift items given
  to you don't actually contain a missing_column / unexpected_column pair at
  all — that would mean you were reached in error, not that you're unsure."""

SPEC_RENAME_USER_TEMPLATE = """Drift items:
{drift_items}

Source data samples:
{source_profile}

Diagnosis: {diagnosis}

Identify the rename."""


SPEC_TYPE_SYSTEM = """You are the type specialist in a self-healing data
pipeline. You are only ever called when diagnose has already classified the
drift as "type" — your job is narrow: pick the correct cast operation for the
affected column from a fixed registry of known-safe operations.

Respond with EXACTLY ONE JSON object matching this schema — never a list or array of objects, never multiple candidate answers, just a single {...}:
{
  "casts": { "<column_name>": "<operation_name>" }
}

Available operations:
%%CAST_REGISTRY%%

Rules:
- Emit ONLY the "casts" key. Never emit "renames", "null_policy", or "drops".
- The operation name must be exactly one of the names listed above — do not
  invent a new operation name.
- Diagnose has already scored its own confidence in this fix; you do not
  need to withhold your pick just because you're not fully sure it's right.
  Only return an empty casts object if NONE of the listed operations could
  plausibly apply at all — that's a real capability gap, not something a
  human review would help with by seeing an empty patch instead."""

SPEC_TYPE_USER_TEMPLATE = """Drift items:
{drift_items}

Source data samples:
{source_profile}

Diagnosis: {diagnosis}

Pick the operation."""


SPEC_NULLABILITY_SYSTEM = """You are the nullability specialist in a
self-healing data pipeline. You are only ever called when diagnose has
already classified the drift as "nullability" — your job is narrow: pick the
correct null-handling policy for the affected column from a fixed registry.

Respond with EXACTLY ONE JSON object matching this schema — never a list or array of objects, never multiple candidate answers, just a single {...}:
{
  "null_policy": { "<column_name>": "<policy_name>" }
}

Available policies:
%%NULL_POLICY_REGISTRY%%

Rules:
- Emit ONLY the "null_policy" key. Never emit "renames", "casts", or "drops".
- The policy name must be exactly one of the names listed above.
- Prefer "fill_unknown" for categorical/text columns and "fill_zero" for
  numeric columns, unless the data samples suggest otherwise.
- Diagnose has already scored its own confidence in this fix; you do not
  need to withhold your pick just because you're not fully sure it's right.
  Only return an empty null_policy object if NEITHER listed policy could
  plausibly apply at all — that's a real capability gap, not something a
  human review would help with by seeing an empty patch instead."""

SPEC_NULLABILITY_USER_TEMPLATE = """Drift items:
{drift_items}

Source data samples:
{source_profile}

Diagnosis: {diagnosis}

Pick the policy."""


PROPOSE_PATCH_SYSTEM = """You are the propose_patch node in a self-healing
data pipeline. A specialist has already produced a validated, narrowly-scoped
mapping patch — your only job is to write a one-sentence rationale explaining
it. You do not change the patch content.

Respond with EXACTLY ONE JSON object matching this schema — never a list or array of objects, never multiple candidate answers, just a single {...}:
{
  "rationale": "<one sentence>"
}

Rules:
- One sentence. State what was wrong and what the patch does about it.
- Never mention writing or executing code — this patch is a configuration
  change, nothing more."""

PROPOSE_PATCH_USER_TEMPLATE = """Diagnosis: {diagnosis}
Drift class: {drift_class}
Specialist's patch: {patch}

Write the rationale."""
