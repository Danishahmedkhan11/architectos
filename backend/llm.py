"""OpenAI integration: GPT-5.6 reasoning + gpt-5.3-codex generation via the
Responses API, with model fallback chains, streaming, embeddings, and an
honest offline demo cache so the product always demos cleanly.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Generator, Optional

from . import config

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore

_client: Optional["OpenAI"] = None
_resolved: dict[str, str] = {}  # kind -> working model id
_demo_cache: Optional[list[dict[str, Any]]] = None


def live_enabled() -> bool:
    if config.DEMO_MODE == "on":
        return False
    return config.LLM_PROVIDER != "none" and OpenAI is not None


def client() -> "OpenAI":
    global _client
    if _client is None:
        if config.LLM_PROVIDER == "openrouter":
            _client = OpenAI(api_key=config.OPENROUTER_API_KEY, base_url=config.OPENROUTER_BASE_URL)
        else:
            _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def engine_info() -> dict[str, Any]:
    return {
        "live": live_enabled(),
        "provider": config.LLM_PROVIDER,
        "reasoning_model": _resolved.get("reason") or _model_chain("reason")[0],
        "codex_model": _resolved.get("codex") or _model_chain("codex")[0],
        "embed_model": config.EMBED_MODEL,
        "mode": "live" if live_enabled() else "demo",
    }


# ------------------------------------------------------------------ streaming

_openrouter_ids: Optional[list[str]] = None  # cached /models listing, None = not fetched


def _openrouter_available_ids() -> list[str]:
    """OpenRouter model ids visible to this key ('openai/gpt-...'). Cached; [] on failure."""
    global _openrouter_ids
    if _openrouter_ids is None:
        try:
            import httpx

            resp = httpx.get(
                f"{config.OPENROUTER_BASE_URL}/models",
                headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                timeout=8,
            )
            resp.raise_for_status()
            _openrouter_ids = [m.get("id", "") for m in resp.json().get("data", [])]
        except Exception:
            _openrouter_ids = []
    return _openrouter_ids


def _openrouter_chain(base_chain: list[str]) -> list[str]:
    """Translate an OpenAI-native chain to OpenRouter's vendor-prefixed namespace.

    Prefers models the account can actually see (from /models); falls back to
    the statically prefixed chain when the listing is unavailable.
    """
    prefixed = [m if "/" in m else f"openai/{m}" for m in base_chain]
    available = _openrouter_available_ids()
    if not available:
        return prefixed
    avail_set = set(available)
    chain = [m for m in prefixed if m in avail_set]
    # any newer openai gpt-5* models the static list doesn't know about yet
    extras = sorted(
        (m for m in available if m.startswith("openai/gpt-5") and m not in chain),
        reverse=True,
    )
    chain += extras
    return chain or prefixed


def _model_chain(kind: str) -> list[str]:
    chain = config.CODEX_MODELS if kind == "codex" else config.REASONING_MODELS
    if config.LLM_PROVIDER == "openrouter":
        chain = _openrouter_chain(chain)
    resolved = _resolved.get(kind)
    if resolved:
        return [resolved] + [m for m in chain if m != resolved]
    return chain


def _is_model_error(err: Exception) -> bool:
    """Errors where trying the next (often cheaper) model in the chain makes sense."""
    msg = str(err).lower()
    return any(s in msg for s in (
        "model", "not found", "does not exist", "invalid", "unsupported",
        "402", "payment", "credit", "afford", "quota", "insufficient",
    ))


def stream_llm(
    kind: str,
    system: str,
    user: str,
    max_output_tokens: int = 4096,
    effort: str = "medium",
) -> Generator[dict[str, Any], None, None]:
    """Yield {'delta': str} chunks, then {'done': True, 'engine': model_id}.

    Falls back through the model chain on model-availability errors; on other
    errors yields {'error': ...}.
    """
    last_err: Optional[Exception] = None
    for model in _model_chain(kind):
        try:
            got_text = False
            if config.LLM_PROVIDER == "openrouter":
                # OpenRouter only implements Chat Completions, not the
                # Responses API — different request shape and stream events.
                def _or_create(budget: int):
                    return client().chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        stream=True,
                        max_tokens=budget,
                    )

                try:
                    stream = _or_create(max_output_tokens)
                except Exception as err:
                    # 402 = can't afford this max_tokens; retry once with a small budget
                    if "402" in str(err) and max_output_tokens > 640:
                        stream = _or_create(640)
                    else:
                        raise
                for chunk in stream:
                    delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                    if delta:
                        got_text = True
                        yield {"delta": delta}
            else:
                kwargs: dict[str, Any] = dict(
                    model=model,
                    instructions=system,
                    input=user,
                    stream=True,
                    max_output_tokens=max_output_tokens,
                )
                if "codex" in model or model.startswith("gpt-5"):
                    kwargs["reasoning"] = {"effort": effort}
                try:
                    stream = client().responses.create(**kwargs)
                except Exception as err:
                    if "reasoning" in kwargs and ("reasoning" in str(err).lower() or "effort" in str(err).lower()):
                        kwargs.pop("reasoning")
                        stream = client().responses.create(**kwargs)
                    else:
                        raise
                for event in stream:
                    etype = getattr(event, "type", "")
                    if etype.endswith("output_text.delta"):
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            got_text = True
                            yield {"delta": delta}
                    elif etype == "response.failed":
                        raise RuntimeError(str(getattr(event, "response", "response.failed")))
            if got_text:
                _resolved[kind] = model
                yield {"done": True, "engine": model}
                return
            last_err = RuntimeError(f"{model} returned no text")
        except Exception as err:  # try next model on availability errors
            last_err = err
            if _is_model_error(err):
                continue
            break
    yield {"error": f"OpenAI call failed: {last_err}", "done": True, "engine": "error"}


# ----------------------------------------------------------------- embeddings

def embed_texts(texts: list[str]) -> Optional[list[list[float]]]:
    # OpenRouter doesn't route the embeddings endpoint — only real OpenAI does.
    if not live_enabled() or not texts or config.LLM_PROVIDER != "openai":
        return None
    try:
        out: list[list[float]] = []
        for i in range(0, len(texts), 128):
            batch = [t[:4000] or " " for t in texts[i: i + 128]]
            resp = client().embeddings.create(model=config.EMBED_MODEL, input=batch)
            out.extend(d.embedding for d in resp.data)
        return out
    except Exception:
        return None


def embed_query(text: str) -> Optional[list[float]]:
    result = embed_texts([text])
    return result[0] if result else None


# ----------------------------------------------------------------- demo cache

def _load_cache() -> list[dict[str, Any]]:
    global _demo_cache
    if _demo_cache is None:
        try:
            _demo_cache = json.loads(config.DEMO_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _demo_cache = []
    return _demo_cache


def _norm_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def find_cached(task: str, prompt: str) -> Optional[dict[str, Any]]:
    """Fuzzy-match a cached demo answer for this task+prompt."""
    q = _norm_tokens(prompt)
    best, best_score = None, 0.0
    for entry in _load_cache():
        if entry.get("task") != task:
            continue
        keys = _norm_tokens(entry.get("prompt", "")) | set(entry.get("keywords", []))
        if not keys:
            continue
        jaccard = len(q & keys) / max(1, len(q | keys))
        keyword_hit = bool(q & set(entry.get("keywords", [])))
        score = jaccard + (0.35 if keyword_hit else 0.0)
        if score > best_score:
            best, best_score = entry, score
    if best and best_score >= 0.3:
        return best
    return None


def stream_cached(entry: dict[str, Any]) -> Generator[dict[str, Any], None, None]:
    """Stream a cached answer in small chunks so the UI feels live."""
    text = entry.get("text", "")
    step = 24
    for i in range(0, len(text), step):
        yield {"delta": text[i: i + step]}
        time.sleep(0.009)
    yield {"done": True, "engine": "demo-cache", "cached": True}


NO_KEY_MESSAGE = (
    "**Offline demo mode** — no API key configured, and this exact "
    "question isn't in the cached demo set.\n\n"
    "The knowledge graph, retrieval, and blast-radius analysis you see are "
    "computed live and fully real. To get live GPT-5.6 reasoning and "
    "gpt-5.3-codex generation for any question, add a key to `.env`:\n\n"
    "```\nOPENAI_API_KEY=sk-...        # native OpenAI (used for the graded demo)\n"
    "OPENROUTER_API_KEY=sk-or-...  # or OpenRouter for cheap testing\n```\n\n"
    "then restart ArchitectOS."
)


def stream_answer(
    task: str,
    system: str,
    user: str,
    cache_prompt: Optional[str] = None,
    max_output_tokens: int = 4096,
    effort: str = "medium",
    kind: str = "reason",
) -> Generator[dict[str, Any], None, None]:
    """Live model if possible, else cached demo answer, else honest fallback.

    In live mode, if the whole model chain fails before producing any text
    (e.g. no credits), fall back to the cached demo answer — clearly labeled.
    """
    if live_enabled():
        failure: Optional[dict[str, Any]] = None
        emitted = False
        for chunk in stream_llm(kind, system, user, max_output_tokens, effort):
            if "error" in chunk and not emitted:
                failure = chunk
                break
            if "delta" in chunk:
                emitted = True
            yield chunk
        if failure is None:
            return
        entry = find_cached(task, cache_prompt or user)
        reason = str(failure.get("error", "unknown error"))
        if entry:
            yield {"delta": f"_⚠ Live model call failed ({reason[:140]}) — showing the cached demo answer instead._\n\n"}
            yield from stream_cached(entry)
            return
        yield {"delta": (
            f"⚠ **Live model call failed.** {reason[:300]}\n\n"
            "The knowledge graph, retrieval, and blast radius above are still fully live. "
            "Add credits to your OpenRouter account or set `OPENAI_API_KEY` in `.env` "
            "for live answers to any question."
        )}
        yield {"done": True, "engine": "error"}
        return
    entry = find_cached(task, cache_prompt or user)
    if entry:
        yield from stream_cached(entry)
        return
    yield from stream_cached({"text": NO_KEY_MESSAGE})
