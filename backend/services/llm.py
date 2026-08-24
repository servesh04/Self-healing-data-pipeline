"""Groq wrapper — the only place in this codebase that talks to an LLM.

ARCHITECTURE.md: "gpt-oss-120b via Groq. Free tier, fast — the heal loop is
iterative." Confirmed against groq==1.6.0: `openai/gpt-oss-120b` is a valid
model literal in this SDK version.

Every call goes through `asyncio.to_thread` (ARCHITECTURE.md, Appendix A —
the Groq SDK is synchronous) and retries on `RateLimitError` with backoff —
the free tier throttles, and a heal cycle can make several calls in one run.
JSON mode (`response_format={"type": "json_object"}`) guarantees syntactically
valid JSON; it does NOT guarantee the JSON matches any particular schema —
every caller must still validate the parsed result against a Pydantic model
(see backend/graph/schemas.py) before using it.
"""

import asyncio
import json
import logging

from groq import AsyncGroq, RateLimitError

from config import get_settings

log = logging.getLogger(__name__)

MODEL = "openai/gpt-oss-120b"
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2.0

_client: AsyncGroq | None = None


class LLMError(Exception):
    """Raised after retries are exhausted, on any non-rate-limit failure, or
    if GROQ_API_KEY isn't configured. Callers (graph nodes) must catch this —
    nodes must never raise (ARCHITECTURE.md, Appendix A) — and route to
    escalate.
    """


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        api_key = get_settings().groq_api_key
        if not api_key:
            raise LLMError(
                "GROQ_API_KEY is not configured -- cannot make LLM calls. "
                "Set it in the environment or .env."
            )
        _client = AsyncGroq(api_key=api_key)
    return _client


async def call_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.1,
) -> dict | list:
    """One JSON-mode completion. Returns the parsed JSON value as-is — a dict
    in the ordinary case, occasionally a list of candidate dicts (see the
    comment inline below). Schema validation and shape interpretation are the
    caller's job; this function's only contract is "valid JSON or LLMError".
    """
    client = _get_client()
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                ),
                timeout=30.0,
            )
            content = response.choices[0].message.content
            # Groq's JSON mode guarantees valid JSON syntax, not shape. This
            # model (openai/gpt-oss-120b) has been observed, consistently,
            # to sometimes wrap its answer as a list of 1-2 candidate objects
            # instead of a single top-level object — confirmed not fixed by
            # reasoning_format="hidden", so it's a genuine output-shape quirk,
            # not a leaked reasoning trace. Returned as-is; interpreting a
            # list of candidates is domain-specific (see
            # graph/schemas.py:normalize_llm_json) and belongs to the caller,
            # not this generic function.
            return json.loads(content)

        except RateLimitError as exc:
            last_exc = exc
            wait = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            log.warning(
                "llm: rate limited (attempt %d/%d), backing off %.1fs",
                attempt, MAX_RETRIES, wait,
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(wait)

        except json.JSONDecodeError as exc:
            # JSON mode guarantees syntax at the API level; this still
            # covers an empty/truncated response defensively.
            last_exc = exc
            log.warning("llm: response was not valid JSON (attempt %d/%d)", attempt, MAX_RETRIES)

        except Exception as exc:  # noqa: BLE001 — genuinely catch-all here
            last_exc = exc
            log.exception("llm: call failed (attempt %d/%d)", attempt, MAX_RETRIES)
            break  # non-rate-limit errors are not worth retrying blindly

    raise LLMError(f"LLM call failed after retries: {last_exc}") from last_exc
