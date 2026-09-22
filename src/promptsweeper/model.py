"""Token counting, pricing and completions.

This module deliberately does not own a price table. When ``opus5lean`` (from
the Claude-Opus5 package) is importable, pricing, exact token counts and the
Messages-API client are delegated to it, because a second copy of a price table
is a second thing to go stale.

When it is absent, a stdlib-only fallback keeps the offline half of this tool
working: variant generation, token estimation and relative cost comparison all
run with no dependencies and no API key.

``HAVE_OPUS5LEAN`` records which path is active so reports can say so rather
than implying a precision they do not have.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

try:  # pragma: no cover - exercised by whichever environment runs the tests
    from opus5lean.client import AnthropicClient as _Opus5Client
    from opus5lean.pricing import Usage as _Opus5Usage
    from opus5lean.pricing import cost as _opus5_cost
    from opus5lean.tokens import count as _opus5_count

    HAVE_OPUS5LEAN = True
except Exception:  # pragma: no cover
    HAVE_OPUS5LEAN = False

ANTHROPIC_MESSAGES = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-5"

#: Fallback rates, USD per million tokens. Only consulted when opus5lean is
#: unavailable; install Claude-Opus5 for the maintained table.
_FALLBACK_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-5-fast": (10.00, 50.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
MILLION = 1_000_000


class ModelError(RuntimeError):
    """A completion could not be obtained."""


@dataclass
class Completion:
    """Normalised result of one model call."""

    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    model: str
    effort: str | None = None
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+|\s+|[^\sA-Za-z\d]+")


def _estimate(text: str) -> int:
    """Offline token estimate. Segments then applies per-class ratios.

    Better than ``len(text) / 4`` because code and punctuation tokenise more
    densely than prose, but still a heuristic — typically within ~10-15%.
    """
    total = 0
    for m in _WORD_RE.finditer(text):
        piece = m.group()
        if piece.isspace():
            total += piece.count("\n") + (1 if piece.strip(" \t") == "" and " " in piece else 0)
        elif piece.isalpha():
            total += 1 if len(piece) <= 5 else max(1, round(len(piece) / 4.2))
        elif piece.isdigit():
            total += max(1, round(len(piece) / 3))
        else:
            total += max(1, round(len(piece) / 2))
    return max(1, total)


def count_tokens(text: str, *, model: str = DEFAULT_MODEL, exact: bool = False) -> tuple[int, str]:
    """Return ``(tokens, method)`` where method is ``"exact"`` or ``"estimate"``.

    Exact counts come from Anthropic's ``count_tokens`` endpoint, which is not
    billed — a key with a zero balance is enough. Callers must keep the method
    attached to the number; an estimate presented as a measurement is a bug.
    """
    if exact and HAVE_OPUS5LEAN:
        try:
            result = _opus5_count(text, model=model, exact=True)
            return int(result.tokens), str(result.method)
        except Exception:
            pass
    return _estimate(text), "estimate"


def price(input_tokens: int, output_tokens: int, *, model: str = DEFAULT_MODEL) -> float:
    """Cost in USD for a given token split."""
    if HAVE_OPUS5LEAN:
        try:
            return float(
                _opus5_cost(
                    _Opus5Usage(input_tokens=input_tokens, output_tokens=output_tokens),
                    model,
                ).total
            )
        except Exception:
            pass
    rate_in, rate_out = _FALLBACK_RATES.get(model, _FALLBACK_RATES[DEFAULT_MODEL])
    return (input_tokens / MILLION) * rate_in + (output_tokens / MILLION) * rate_out


# ---------------------------------------------------------------------------
# Completions
# ---------------------------------------------------------------------------


def _post(payload: dict, api_key: str, timeout: float) -> dict:
    req = urllib.request.Request(
        ANTHROPIC_MESSAGES,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise ModelError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise ModelError(f"request failed: {exc}") from exc


def complete(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    effort: str | None = None,
    max_tokens: int = 4096,
    timeout: float = 600.0,
) -> Completion:
    """Run one completion.

    Prefers ``opus5lean``'s client, which selects content blocks by ``type``.
    That detail is not cosmetic: on Opus 5 thinking is on by default, so
    ``content[0]`` is frequently a thinking block rather than text, and
    position-based extraction silently returns the wrong thing.
    """
    if HAVE_OPUS5LEAN:
        try:
            client = _Opus5Client()
            kwargs: dict = {"model": model, "system": system, "max_tokens": max_tokens}
            if effort:
                kwargs["effort"] = effort
            resp = client.complete(prompt, **kwargs)
            return Completion(
                text=resp.text,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cost_usd=price(resp.usage.input_tokens, resp.usage.output_tokens, model=model),
                latency_s=resp.latency_s,
                model=model,
                effort=getattr(resp, "effort", effort),
                raw=getattr(resp, "raw", {}) or {},
            )
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError(f"opus5lean client failed: {exc}") from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ModelError(
            "ANTHROPIC_API_KEY is not set. Offline commands (generate, estimate) "
            "need no key; `run` makes real billed calls and does."
        )

    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system
    if effort:
        payload["output_config"] = {"effort": effort}

    start = time.perf_counter()
    body = _post(payload, api_key, timeout)
    latency = time.perf_counter() - start

    text = "".join(
        block.get("text", "")
        for block in body.get("content", [])
        if block.get("type") == "text"
    )
    usage = body.get("usage", {}) or {}
    in_tok = int(usage.get("input_tokens", 0) or 0)
    out_tok = int(usage.get("output_tokens", 0) or 0)

    return Completion(
        text=text,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=price(in_tok, out_tok, model=model),
        latency_s=latency,
        model=model,
        effort=effort,
        raw=body,
    )
