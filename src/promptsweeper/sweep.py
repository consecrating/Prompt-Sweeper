"""Sweep orchestration: run every variant, score it, pick a winner.

The selection rule is "cheapest variant that clears the quality threshold", not
"highest score". Highest-score selection quietly pushes you toward the most
verbose prompt, because more scaffolding tends to score marginally better while
costing substantially more. Cheapest-passing keeps the win honest.

Three refusals are built in, because each corresponds to a way this tool could
produce a confident lie:

* no grader supplied -> report cost and latency, recommend nothing
* grader is degenerate -> same, and say why
* nothing clears the threshold -> report the gap, recommend nothing
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .model import DEFAULT_MODEL, Completion, ModelError, complete, count_tokens
from .score import Grader, is_degenerate
from .variants import REDUNDANT_WITH_THINKING, Variant, generate

#: Signature of the model call. Injectable so tests and offline demos can stub it
#: without monkeypatching a module global.
CompleteFn = Callable[..., Completion]


@dataclass
class Trial:
    """One execution of one variant."""

    text: str
    score: float
    cost_usd: float
    latency_s: float
    input_tokens: int
    output_tokens: int


@dataclass
class VariantResult:
    """Aggregate of all trials for one variant."""

    variant: Variant
    trials: list[Trial] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def strategy(self) -> str:
        return self.variant.strategy

    @property
    def ok(self) -> bool:
        return bool(self.trials)

    @property
    def mean_score(self) -> float:
        return statistics.fmean(t.score for t in self.trials) if self.trials else 0.0

    @property
    def worst_score(self) -> float:
        return min((t.score for t in self.trials), default=0.0)

    @property
    def mean_cost(self) -> float:
        return statistics.fmean(t.cost_usd for t in self.trials) if self.trials else 0.0

    @property
    def mean_latency(self) -> float:
        return statistics.fmean(t.latency_s for t in self.trials) if self.trials else 0.0

    @property
    def mean_output_tokens(self) -> float:
        return statistics.fmean(t.output_tokens for t in self.trials) if self.trials else 0.0

    @property
    def best_text(self) -> str:
        if not self.trials:
            return ""
        return max(self.trials, key=lambda t: t.score).text


@dataclass
class SweepResult:
    """Full sweep, plus the recommendation and the reason for any refusal."""

    task: str
    model: str
    results: list[VariantResult]
    threshold: float
    graded: bool
    require_all_trials: bool = False
    grader_degenerate: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def by_strategy(self) -> dict[str, VariantResult]:
        return {r.strategy: r for r in self.results}

    @property
    def baseline(self) -> VariantResult | None:
        return self.by_strategy.get("baseline")

    @property
    def passing(self) -> list[VariantResult]:
        """Variants clearing the threshold, cheapest first."""
        if not self.graded or self.grader_degenerate:
            return []
        metric = (lambda r: r.worst_score) if self.require_all_trials else (lambda r: r.mean_score)
        winners = [r for r in self.results if r.ok and metric(r) >= self.threshold]
        return sorted(winners, key=lambda r: (r.mean_cost, r.variant.tokens))

    @property
    def recommended(self) -> VariantResult | None:
        """Cheapest passing variant, or ``None`` if the sweep refuses to pick."""
        return self.passing[0] if self.passing else None

    @property
    def refusal(self) -> str | None:
        """Why no recommendation was made, or ``None`` when one was."""
        if self.recommended is not None:
            return None
        if not self.graded:
            return (
                "no grader supplied — cost and latency are reported, but choosing a "
                "prompt on cost alone is how a quality regression ships"
            )
        if self.grader_degenerate:
            return (
                "grader is degenerate: it scores empty and nonsense responses as "
                "passing, so it cannot separate variants"
            )
        best = max((r.mean_score for r in self.results if r.ok), default=0.0)
        return f"no variant cleared threshold {self.threshold:.2f} (best was {best:.2f})"

    def savings_vs_baseline(self) -> float | None:
        """Fractional cost reduction of the recommendation against baseline."""
        rec, base = self.recommended, self.baseline
        if not rec or not base or not base.mean_cost:
            return None
        return 1 - (rec.mean_cost / base.mean_cost)


def estimate(
    task: str,
    *,
    model: str = DEFAULT_MODEL,
    only: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    expected_output_tokens: int = 600,
    exact: bool = False,
) -> tuple[list[Variant], str]:
    """Generate variants and attach token counts, with no API calls.

    Returns ``(variants, method)`` where method is ``"exact"`` or ``"estimate"``.
    """
    variants = generate(task, only=only, exclude=exclude)
    method = "estimate"
    counted: list[Variant] = []
    for v in variants:
        tokens, method = count_tokens(v.prompt, model=model, exact=exact)
        counted.append(v.with_tokens(tokens))
    return counted, method


def sweep(
    task: str,
    *,
    model: str = DEFAULT_MODEL,
    grader: Grader | None = None,
    threshold: float = 1.0,
    trials: int = 1,
    only: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    system: str | None = None,
    effort: str | None = None,
    max_tokens: int = 4096,
    require_all_trials: bool = False,
    skip_redundant: bool = False,
    on_progress=None,
    complete_fn: CompleteFn | None = None,
) -> SweepResult:
    """Run every variant ``trials`` times and score the results.

    Args:
        grader: Scores responses in [0, 1]. Without one, no winner is chosen.
        threshold: Minimum score for a variant to count as passing.
        trials: Repeats per variant. Output varies run to run, so more than one
            is worth it before acting on small differences.
        require_all_trials: Judge on the worst trial instead of the mean, so a
            variant that passes only intermittently does not win.
        skip_redundant: Drop strategies that Opus 5's default thinking already
            covers.
        on_progress: Called as ``(strategy, trial_index, total_trials)``.
        complete_fn: Override the model call. Injected rather than
            monkeypatched, because ``promptsweeper.sweep`` the function shadows
            this module in the package namespace, which makes patching a module
            global quietly ineffective. Tests and offline demos pass a stub here.

    Raises:
        ValueError: If ``trials`` < 1 or ``threshold`` is outside [0, 1].
    """
    if trials < 1:
        raise ValueError("trials must be >= 1")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")

    notes: list[str] = []
    excluded = list(exclude or [])
    if skip_redundant:
        dropped = sorted(REDUNDANT_WITH_THINKING - set(excluded))
        if dropped:
            excluded.extend(dropped)
            notes.append(
                "skipped "
                + ", ".join(dropped)
                + " — Opus 5 thinks by default, so these mostly duplicate work you are billed for"
            )

    variants, _ = estimate(task, model=model, only=only, exclude=excluded)
    call = complete_fn or complete

    degenerate = bool(grader) and is_degenerate(grader)
    if degenerate:
        notes.append("grader accepted empty and nonsense input; treat scores as meaningless")

    results: list[VariantResult] = []
    for v in variants:
        vr = VariantResult(variant=v)
        for i in range(trials):
            if on_progress:
                on_progress(v.strategy, i + 1, trials)
            try:
                c: Completion = call(
                    v.prompt,
                    model=model,
                    system=system,
                    effort=effort,
                    max_tokens=max_tokens,
                )
            except ModelError as exc:
                vr.errors.append(str(exc))
                continue
            score = 0.0
            if grader:
                try:
                    score = max(0.0, min(1.0, float(grader(c.text))))
                except Exception as exc:  # a broken grader must not kill the sweep
                    vr.errors.append(f"grader raised: {exc}")
            vr.trials.append(
                Trial(
                    text=c.text,
                    score=score,
                    cost_usd=c.cost_usd,
                    latency_s=c.latency_s,
                    input_tokens=c.input_tokens,
                    output_tokens=c.output_tokens,
                )
            )
        results.append(vr)

    return SweepResult(
        task=task,
        model=model,
        results=results,
        threshold=threshold,
        graded=grader is not None,
        require_all_trials=require_all_trials,
        grader_degenerate=degenerate,
        notes=notes,
    )
