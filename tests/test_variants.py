"""Variant generation tests. Offline, no key, no network."""

from __future__ import annotations

import pytest

from promptsweeper.variants import (
    REDUNDANT_WITH_THINKING,
    STRATEGIES,
    generate,
    s_minimal,
)

TASK = "Please could you write a Python function that parses an ISO date string and also handles errors"


def test_generates_every_strategy_by_default():
    variants = generate(TASK)
    assert len(variants) == len(STRATEGIES)
    assert [v.strategy for v in variants] == list(STRATEGIES)


def test_baseline_is_verbatim():
    baseline = next(v for v in generate(TASK) if v.strategy == "baseline")
    assert baseline.prompt == TASK


def test_every_variant_documents_itself():
    for v in generate(TASK):
        assert v.fixes.strip(), f"{v.strategy} does not say what it fixes"
        assert v.wasteful_when.strip(), f"{v.strategy} does not say when it is wasteful"


def test_every_variant_is_non_empty():
    for v in generate(TASK):
        assert v.prompt.strip()


def test_minimal_strips_filler_but_keeps_the_task():
    out = s_minimal(TASK).prompt.lower()
    assert "please" not in out
    assert "could you" not in out
    assert "parses an iso date string" in out


def test_minimal_never_empties_a_pure_filler_task():
    # Degenerate input: everything is filler. Must not produce an empty prompt.
    assert s_minimal("Please could you kindly help me").prompt is not None


def test_only_filters_and_preserves_registry_order():
    variants = generate(TASK, only=["cot", "baseline", "direct"])
    assert [v.strategy for v in variants] == ["baseline", "direct", "cot"]


def test_exclude_filters():
    variants = generate(TASK, exclude=["baseline", "cot"])
    names = {v.strategy for v in variants}
    assert "baseline" not in names
    assert "cot" not in names


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match="unknown strategies"):
        generate(TASK, only=["does-not-exist"])
    with pytest.raises(ValueError, match="unknown strategies"):
        generate(TASK, exclude=["also-not-real"])


def test_empty_task_is_rejected():
    for bad in ("", "   ", "\n"):
        with pytest.raises(ValueError, match="non-empty"):
            generate(bad)


def test_spec_enumerates_requirements_and_criteria():
    spec = next(v for v in generate(TASK) if v.strategy == "spec")
    assert "R1." in spec.prompt
    assert "- [ ]" in spec.prompt


def test_redundant_set_names_real_strategies():
    assert set(STRATEGIES) >= REDUNDANT_WITH_THINKING


def test_with_tokens_is_immutable_and_copies_fields():
    v = generate(TASK, only=["direct"])[0]
    counted = v.with_tokens(42)
    assert v.tokens == 0
    assert counted.tokens == 42
    assert counted.strategy == v.strategy
    assert counted.fixes == v.fixes


def test_code_fence_in_task_survives_generation():
    task = "Fix this:\n```python\nx=1\n```\nmake it typed"
    for v in generate(task):
        if v.strategy in ("baseline", "minimal", "direct"):
            assert "x=1" in v.prompt
