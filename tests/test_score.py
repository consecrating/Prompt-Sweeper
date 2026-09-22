"""Grader tests, including the degenerate-grader guard."""

from __future__ import annotations

import pytest

from promptsweeper.score import (
    Check,
    build,
    composite,
    forbidden,
    is_degenerate,
    matches,
    no_placeholders,
    parses_python,
    required,
    valid_json,
    word_budget,
)


def test_required_is_fractional():
    grade = required(["def", "return", "raise"])
    assert grade("def f(): return 1") == pytest.approx(2 / 3)
    assert grade("def f(): return 1\n raise ValueError") == 1.0
    assert grade("nothing here") == 0.0


def test_required_is_case_insensitive():
    assert required(["RETURN"])("we return early") == 1.0


def test_forbidden_is_binary():
    grade = forbidden(["TODO", "FIXME"])
    assert grade("clean code") == 1.0
    assert grade("# TODO: later") == 0.0


def test_empty_criteria_are_rejected():
    with pytest.raises(ValueError):
        required([])
    with pytest.raises(ValueError):
        forbidden([])


def test_matches_regex():
    grade = matches(r"def\s+\w+\(")
    assert grade("def parse(x):") == 1.0
    assert grade("class Parse:") == 0.0


def test_valid_json_accepts_bare_and_fenced():
    grade = valid_json()
    assert grade('{"a": 1}') == 1.0
    assert grade('```json\n{"a": 1}\n```') == 1.0
    assert grade("not json at all") == 0.0


def test_parses_python_detects_syntax_errors():
    grade = parses_python()
    assert grade("```python\ndef f():\n    return 1\n```") == 1.0
    assert grade("```python\ndef f(:\n```") == 0.0
    # No code block at all is a failure, not a pass.
    assert grade("here is how you would do it") == 0.0


def test_parses_python_is_fractional_across_blocks():
    text = "```python\nx = 1\n```\ntext\n```python\ndef bad(:\n```"
    assert parses_python()(text) == pytest.approx(0.5)


def test_no_placeholders():
    grade = no_placeholders()
    assert grade("def f(): return 1") == 1.0
    assert grade("def f(): ...") == 0.0
    assert grade("# TODO implement") == 0.0


def test_word_budget_degrades_outside_range():
    grade = word_budget(10)
    assert grade("one two three") == 1.0
    assert grade(" ".join(["w"] * 20)) == pytest.approx(0.5)


def test_word_budget_validates_bounds():
    with pytest.raises(ValueError):
        word_budget(0)
    with pytest.raises(ValueError):
        word_budget(5, minimum=10)


def test_composite_is_weighted():
    grade = composite([
        Check("a", lambda t: 1.0, 3.0),
        Check("b", lambda t: 0.0, 1.0),
    ])
    assert grade("x") == pytest.approx(0.75)


def test_composite_rejects_empty_and_zero_weights():
    with pytest.raises(ValueError):
        composite([])
    with pytest.raises(ValueError):
        composite([Check("a", lambda t: 1.0, 0.0)])


def test_build_returns_none_without_criteria():
    # This is the contract that keeps "ungraded" from meaning "everything passed".
    assert build() is None


def test_build_assembles_from_flags():
    grade = build(require=["def"], python_code=True, reject_placeholders=True)
    assert grade is not None
    assert grade("```python\ndef f():\n    return 1\n```") == 1.0
    assert grade("```python\ndef f():\n    ...\n```") < 1.0


def test_is_degenerate_flags_always_passing_graders():
    assert is_degenerate(lambda text: 1.0) is True
    assert is_degenerate(required(["def"])) is False


def test_is_degenerate_survives_a_throwing_grader():
    def boom(text: str) -> float:
        raise RuntimeError("bad grader")

    assert is_degenerate(boom) is False
