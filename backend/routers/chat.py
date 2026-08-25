"""POST /api/chat/{run_id} (per-run) and POST /api/chat (global) — read-only
chat over run data.

HARD BOUNDARY (new feature — read this before changing anything here):
this is a single LLM call with assembled context, NOT a LangGraph graph.
Grep this file: there is no `tools=`/`functions=` argument anywhere, and
services/llm.py's call_json doesn't even expose one — the model receives a
context blob and returns text, nothing else. It cannot trigger a run,
approve, or reject a patch. Does not touch backend/graph/** or
backend/pipeline/**; the graph doesn't know this feature exists.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from auth import require_token
from prompts.prompts import CHAT_SYSTEM_GLOBAL, CHAT_SYSTEM_PER_RUN, CHAT_USER_TEMPLATE
from services.chat_context import build_global_context, build_run_context, estimate_tokens
from services.llm import LLMError, call_json

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(require_token)])

MAX_MESSAGE_CHARS = 500
# "Last 10 conversation turns" -- interpreted as the last 10 *messages* in
# the request payload (the field is literally named `messages`), enforced
# defensively here even though the frontend is expected to already trim to
# this before sending.
MAX_HISTORY_MESSAGES = 10


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class ChatReplyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str


def _graph_app(request: Request):
    return request.app.state.graph_app


def _transcript(messages: list[ChatMessage]) -> str:
    recent = messages[-MAX_HISTORY_MESSAGES:]
    speaker = {"user": "User", "assistant": "Assistant"}
    return "\n".join(f"{speaker[m.role]}: {m.content}" for m in recent)


def _normalize_llm_json(raw) -> list[dict]:
    # Same defensive backstop as graph/schemas.py:normalize_llm_json for the
    # Groq list-wrapping quirk (services/llm.py's docstring) -- duplicated
    # rather than imported since that module lives under backend/graph/,
    # which this feature must not import from.
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


async def _ask(system_prompt: str, context: dict, messages: list[ChatMessage]) -> dict:
    """Shared by both endpoints: format the prompt, call the LLM, validate
    the {"reply": ...} shape, return {"reply", "tokens_used"}.
    """
    context_json = json.dumps(context, indent=2)
    user_prompt = CHAT_USER_TEMPLATE.format(
        context_json=context_json,
        transcript=_transcript(messages),
    )

    try:
        raw = await call_json(system_prompt, user_prompt, temperature=0.2)
    except LLMError as exc:
        log.exception("chat: LLM call failed")
        raise HTTPException(
            status_code=503, detail=f"chat is temporarily unavailable: {exc}"
        ) from exc

    for candidate in _normalize_llm_json(raw):
        try:
            parsed = ChatReplyOutput.model_validate(candidate)
        except Exception:  # noqa: BLE001 -- try the next candidate, if any
            continue
        # No real usage.total_tokens available: call_json returns only the
        # parsed JSON body, not the raw Groq response object, and this
        # feature's file boundary doesn't include modifying services/llm.py
        # to surface it. Estimated the same way services/chat_context.py
        # estimates the context budget -- an approximation, not a metered
        # count, and documented as such here rather than silently implied
        # to be exact.
        tokens_used = estimate_tokens(system_prompt) + estimate_tokens(user_prompt) + estimate_tokens(parsed.reply)
        return {"reply": parsed.reply, "tokens_used": tokens_used}

    log.error("chat: no valid {reply: ...} candidate in LLM output: %r", raw)
    raise HTTPException(status_code=502, detail="chat model returned an unexpected response shape")


@router.post("/{run_id}")
async def chat_about_run(
    run_id: str, body: ChatRequest, graph_app=Depends(_graph_app)
) -> dict:
    if not body.messages or body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="last message must be from the user")

    context = await build_run_context(run_id, graph_app)
    if context is None:
        raise HTTPException(status_code=404, detail="run not found")

    return await _ask(CHAT_SYSTEM_PER_RUN, context, body.messages)


@router.post("")
async def chat_global(
    body: ChatRequest,
    graph_app=Depends(_graph_app),
    status: str | None = Query(None, description="filter run records by status"),
    dataset: str | None = Query(None, description="filter run records by dataset filename"),
) -> dict:
    if not body.messages or body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="last message must be from the user")

    context = await build_global_context(graph_app, status=status, dataset=dataset)
    return await _ask(CHAT_SYSTEM_GLOBAL, context, body.messages)
