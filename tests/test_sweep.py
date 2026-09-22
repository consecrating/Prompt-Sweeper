"""Sweep tests. A stub completion function keeps these offline and free.

The behaviour under test is mostly the *refusals*: this tool is only useful if
it declines to recommend a prompt when the evidence does not support one.

Note that the model call is injected via ``complete_fn`` rather than
monkeypatched. ``promptsweeper.sweep`` the function shadows the module of the
same name in the package namespace, so patching a module global is quietly
ineffective — injection removes that trap entirely.
"""

from __future__ import annotations

import pytest

from promptsweeper import score
from promptsweeper.model import Completion, ModelError
from promptsweeper.sweep import estimate, sweep

TASK = "Write a Python function that parses an ISO date string and handles errors"

PASSING = "```python\ndef f():\n    return 1\n```"


def stub(*, per_prompt=None, default=PASSING, sequence=None):
    """Build a deterministic stand-in for the model call.

    Args:
        per_prompt: ``{marker: response}`` — first marker found in the prompt wins.
        default: Response when no marker matches.
        sequence: Iterable of responses handed out in order, ignoring the prompt.
    """
    per_prompt = per_prompt or {}
    it = iter(sequence) if sequence is not None else None

    def call(prompt: str, **kwargs) -> Completion:
        if it is not None:
            text = next(it)
        else:
            text = default
            for marker, value in per_prompt.items():
                if marker in prompt:
                    text = value
                    break
        if isinstance(text, Exception):
            raise text
        return Completion(
            text=text,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            # Longer prompts must genuinely cost more for selection to mean anything.
            cost_usd=len(prompt) / 100_000,
            latency_s=0.01,
            model=kwargs.get("model", "claude-opus-5"),
        )

    return call


# ---------------------------------------------------------------------------
# estimate (pure offline)
# ---------------------------------------------------------------------------


def test_estimate_attaches_token_counts_without_calls():
    variants, method = estimate(TASK)
    assert method in ("estimate", "exact")
    assert all(v.tokens > 0 for v in variants)


def test_estimate_respects_only():
    variants, _ = estimate(TASK, only=["baseline", "direct"])
    assert [v.strategy for v in variants] == ["baseline", "direct"]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_invalid_trials_and_threshold_are_rejected():
    with pytest.raises(ValueError, match="trials"):
        sweep(TASK, trials=0, complete_fn=stub())
    with pytest.raises(ValueError, match="threshold"):
        sweep(TASK, threshold=1.5, complete_fn=stub())


# ---------------------------------------------------------------------------
# refusals — the core value of this tool
# ---------------------------------------------------------------------------


def test_ungraded_sweep_refuses_to_recommend():
    result = sweep(TASK, only=["baseline", "direct"], complete_fn=stub())
    assert result.graded is False
    assert result.recommended is None
    assert "no grader" in result.refusal


def test_degenerate_grader_refuses_to_recommend():
    result = sweep(
        TASK, grader=lambda t: 1.0, only=["baseline", "direct"], complete_fn=stub()
    )
    assert result.grader_degenerate is True
    assert result.recommended is None
    assert "degenerate" in result.refusal


def test_nothing_passing_refuses_and_reports_the_gap():
    result = sweep(
        TASK,
        grader=score.required(["def", "return"]),
        threshold=1.0,
        only=["baseline", "direct"],
        complete_fn=stub(default="totally unrelated prose"),
    )
    assert result.recommended is None
    assert "no variant cleared threshold" in result.refusal


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def test_picks_cheapest_passing_variant_not_highest_scoring():
    # Every variant returns identical passing output, so cost is the only
    # differentiator. Baseline has the shortest prompt, so it must win.
    result = sweep(
        TASK,
        grader=score.required(["def", "return"]),
        only=["baseline", "spec", "few_shot"],
        complete_fn=stub(),
    )
    assert result.recommended is not None
    assert result.recommended.strategy == "baseline"


def test_passing_list_is_ordered_cheapest_first():
    result = sweep(
        TASK,
        grader=score.required(["def"]),
        only=["baseline", "spec", "few_shot"],
        complete_fn=stub(),
    )
    costs = [r.mean_cost for r in result.passing]
    assert costs == sorted(costs)


def test_mean_mode_accepts_intermittent_passes():
    result = sweep(
        TASK,
        grader=score.required(["def", "return"]),
        trials=2,
        only=["baseline"],
        threshold=0.5,
        complete_fn=stub(sequence=[PASSING, "nope"]),
    )
    assert result.recommended is not None  # mean of 1.0 and 0.0 clears 0.5


def test_all_trials_mode_rejects_intermittent_passes():
    result = sweep(
        TASK,
        grader=score.required(["def", "return"]),
        trials=2,
        only=["baseline"],
        threshold=0.5,
        require_all_trials=True,
        complete_fn=stub(sequence=[PASSING, "nope"]),
    )
    assert result.recommended is None  # worst trial was 0.0


def test_skip_redundant_drops_thinking_covered_strategies():
    result = sweep(
        TASK, grader=score.required(["def"]), skip_redundant=True, complete_fn=stub()
    )
    names = {r.strategy for r in result.results}
    assert "cot" not in names
    assert "self_check" not in names
    assert any("skipped" in n for n in result.notes)


def test_grader_rewarding_scaffolding_beats_baseline():
    # The one case where a more expensive prompt should win: baseline fails.
    result = sweep(
        TASK,
        grader=score.required(["def", "return"]),
        only=["baseline", "negative"],
        complete_fn=stub(per_prompt={"Do not:": PASSING}, default="prose only"),
    )
    assert result.recommended is not None
    assert result.recommended.strategy == "negative"
    saving = result.savings_vs_baseline()
    assert saving is not None and saving < 0  # honestly reported as more expensive


# ---------------------------------------------------------------------------
# resilience
# ---------------------------------------------------------------------------


def test_api_errors_are_captured_not_raised():
    result = sweep(
        TASK,
        grader=score.required(["def"]),
        only=["baseline"],
        complete_fn=stub(default=ModelError("HTTP 429: slow down")),
    )
    assert result.results[0].ok is False
    assert "429" in result.results[0].errors[0]
    assert result.recommended is None


def test_a_throwing_grader_does_not_kill_the_sweep():
    def boom(text: str) -> float:
        raise RuntimeError("grader exploded")

    result = sweep(TASK, grader=boom, only=["baseline"], complete_fn=stub())
    assert result.results[0].ok is True
    assert any("grader raised" in e for e in result.results[0].errors)


def test_progress_callback_fires_per_trial():
    seen: list[tuple[str, int, int]] = []
    sweep(
        TASK,
        grader=score.required(["def"]),
        only=["baseline", "direct"],
        trials=2,
        on_progress=lambda s, i, t: seen.append((s, i, t)),
        complete_fn=stub(),
    )
    assert len(seen) == 4
    assert seen[0] == ("baseline", 1, 2)


def test_best_text_returns_highest_scoring_trial():
    result = sweep(
        TASK,
        grader=score.required(["def", "return"]),
        trials=2,
        only=["baseline"],
        threshold=0.4,
        complete_fn=stub(sequence=["nope", PASSING]),
    )
    assert "def f()" in result.results[0].best_text
