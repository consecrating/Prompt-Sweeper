#!/usr/bin/env python3
"""End-to-end sweep with a stubbed model — no API key, no cost.

Demonstrates the whole loop (generate -> run -> score -> select -> remember)
using a fake client, so the mechanics can be inspected without spending
anything. Swap ``fake_complete`` for the real client and the flow is identical.

    python examples/offline_sweep.py
"""

from __future__ import annotations

import os
import tempfile

# Keep the demo's ledger out of the real one.
os.environ["PROMPTSWEEPER_STORE"] = os.path.join(tempfile.gettempdir(), "promptsweeper-demo.json")

from promptsweeper import score, store  # noqa: E402
from promptsweeper.model import Completion  # noqa: E402
from promptsweeper.report import render_sweep, render_variants  # noqa: E402
from promptsweeper.sweep import estimate  # noqa: E402
from promptsweeper.sweep import sweep as run_sweep  # noqa: E402

TASK = "Please could you write a Python function that retries a callable with exponential backoff"

GOOD = '''```python
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def retry(fn: Callable[[], T], *, attempts: int = 3, base: float = 0.5) -> T:
    """Call fn, retrying with exponential backoff."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(base * (2 ** i))
    raise last
```
- Assumes all exceptions are retryable
'''

LAZY = '''```python
def retry(fn):
    # TODO: add backoff
    ...
```
'''


def fake_complete(prompt: str, **kwargs) -> Completion:
    """Stand-in for the real model.

    Rewards prompts that explicitly forbid placeholders; everything else gets
    the lazy answer. Crude, but it makes the selection logic observable.
    """
    text = GOOD if "Do not:" in prompt or "placeholder" in prompt.lower() else LAZY
    in_tok = max(1, len(prompt) // 4)
    out_tok = max(1, len(text) // 4)
    return Completion(
        text=text,
        input_tokens=in_tok,
        output_tokens=out_tok,
        # Price it so longer prompts genuinely cost more.
        cost_usd=in_tok * 5 / 1_000_000 + out_tok * 25 / 1_000_000,
        latency_s=0.5 + in_tok / 400,
        model=kwargs.get("model", "claude-opus-5"),
    )


def main() -> int:
    print("=" * 78)
    print("STEP 1 — generate variants (offline, free)")
    print("=" * 78)
    variants, method = estimate(TASK)
    print(render_variants(variants, method=method, model="claude-opus-5"))

    print()
    print("=" * 78)
    print("STEP 2 — build a grader")
    print("=" * 78)
    grader = score.build(
        require=["def", "return"],
        python_code=True,
        reject_placeholders=True,
    )
    print("  checks: required substrings, code must parse, no placeholders")
    print(f"  degenerate? {score.is_degenerate(grader)}  (False means it can actually fail)")

    print()
    print("=" * 78)
    print("STEP 3 — sweep with a stubbed model")
    print("=" * 78)
    result = run_sweep(
        TASK,
        grader=grader,
        threshold=1.0,
        trials=1,
        skip_redundant=True,
        complete_fn=fake_complete,  # the only argument a real run would drop
    )
    print(render_sweep(result))

    print()
    print("=" * 78)
    print("STEP 4 — what an ungraded sweep does instead")
    print("=" * 78)
    ungraded = run_sweep(
        TASK, only=["baseline", "direct", "negative"], complete_fn=fake_complete
    )
    print(f"  recommended: {ungraded.recommended}")
    print(f"  refusal:     {ungraded.refusal}")

    print()
    print("=" * 78)
    print("STEP 5 — remember the winner")
    print("=" * 78)
    rec = result.recommended
    if not rec:
        print("  nothing to record — the sweep declined to pick a winner")
        return 1

    winner = store.Winner(
        task_kind=store.classify(TASK),
        strategy=rec.strategy,
        model=result.model,
        score=rec.mean_score,
        cost_usd=rec.mean_cost,
        savings_vs_baseline=result.savings_vs_baseline(),
        trials=len(rec.trials),
        graded=result.graded,
        task_excerpt=TASK[:160],
    )
    info = store.record(winner, to_aibrain=False)  # demo: do not touch the real brain
    print(f"  ledger:  {info['ledger']} ({info['entries']} entries)")

    remembered = store.recall(TASK)
    print(f"  recall:  {remembered['task_kind']} -> {remembered['strategy']}")
    print()
    print("  A real run would add --save to write this into AIBrain's decision log.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
