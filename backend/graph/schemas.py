"""Pydantic models for every LLM node's output. Every LLM node uses JSON mode
*and* validates against one of these before returning — JSON mode guarantees
syntax, not schema (ARCHITECTURE.md, Appendix A).

The specialist models are deliberately narrow, one per mapping section, each
with `extra="forbid"` — this is the actual enforcement of "a specialist must
not emit keys outside its section" (ARCHITECTURE.md, propose_patch's node
spec). Validating a specialist's raw output against the *general* MappingPatch
model (backend/pipeline/mapping.py) is not enough to catch this: MappingPatch
declares all four sections as optional with empty defaults, so a rename
specialist that also emits a `casts` key would pass MappingPatch validation
silently — the leaked key just looks like a small, harmless, unrelated patch
fragment. It only breaks something later: propose_patch merging it in, or a
downstream router assuming a rename-only decision produced a rename-only
patch. Each specialist's raw output is validated here, against its own
schema, before propose_patch ever sees it — a leaked key fails immediately
and by name, not silently three nodes later.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pipeline.mapping import CAST_REGISTRY, NULL_POLICY_REGISTRY


class DiagnoseOutput(BaseModel):
    """diagnose's output. `confidence` calibration is the entire escalation
    story — see prompts.py's DIAGNOSE_PROMPT and the explicit instruction to
    prefer under-confidence over a wrong auto-apply.
    """

    model_config = ConfigDict(extra="forbid")

    drift_class: str = Field(pattern="^(rename|type|nullability|unknown)$")
    diagnosis: str
    confidence: float = Field(ge=0.0, le=1.0)


class RenameSpecialistOutput(BaseModel):
    """Scoped to the `renames` section only."""

    model_config = ConfigDict(extra="forbid")

    renames: dict[str, str] = {}


class TypeSpecialistOutput(BaseModel):
    """Scoped to the `casts` section only. Op names are checked against the
    same registry apply_mapping actually resolves against — a specialist
    naming a cast operation that doesn't exist fails here, not at apply time.
    """

    model_config = ConfigDict(extra="forbid")

    casts: dict[str, str] = {}

    @field_validator("casts")
    @classmethod
    def _known_ops(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(v.values()) - set(CAST_REGISTRY))
        if unknown:
            raise ValueError(f"unknown cast op(s) {unknown}; available: {sorted(CAST_REGISTRY)}")
        return v


class NullabilitySpecialistOutput(BaseModel):
    """Scoped to the `null_policy` section only."""

    model_config = ConfigDict(extra="forbid")

    null_policy: dict[str, str] = {}

    @field_validator("null_policy")
    @classmethod
    def _known_ops(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(v.values()) - set(NULL_POLICY_REGISTRY))
        if unknown:
            raise ValueError(
                f"unknown null_policy op(s) {unknown}; available: {sorted(NULL_POLICY_REGISTRY)}"
            )
        return v


# drift_class -> the specialist output model that must validate its raw
# output before propose_patch (or anything else) ever touches it.
SPECIALIST_SCHEMAS: dict[str, type[BaseModel]] = {
    "rename": RenameSpecialistOutput,
    "type": TypeSpecialistOutput,
    "nullability": NullabilitySpecialistOutput,
}


class ProposePatchOutput(BaseModel):
    """Validates propose_patch's RAW LLM response only — the rationale, and
    nothing else. The patch content is never re-derived from this call; it's
    already the specialist's validated output, merged in by the node after
    this model passes. `extra="forbid"` here means a propose_patch call that
    tries to also emit its own "patch" key — inventing content instead of
    just explaining the specialist's — fails validation immediately, the
    same scope-leakage protection the specialist models provide one node
    earlier.
    """

    model_config = ConfigDict(extra="forbid")

    rationale: str
