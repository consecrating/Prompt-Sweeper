"""Graders: turn a response into a number in [0, 1].

The failure mode this module exists to prevent is self-deception. A sweep that
scores every variant 1.0 has measured nothing, and will confidently recommend
whichever variant happened to be cheapest. So:

* graders are explicit and deterministic,
* a sweep with no grader refuses to recommend a winner,
* a grader that cannot fail on any input is reported as degenerate.

Cheap deterministic checks first. If they cannot express your quality bar, write
a custom grader and pass it to :func:`~promptsweeper.sweep.sweep`; do not stretch
a substring match into something it is not.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

#: A grader maps response text to a score in [0, 1].
Grader = Callable[[str], float]


@dataclass(frozen=True)
class Check:
    """One named component of a composite grader."""

    name: str
    fn: Grader
    weight: float = 1.0


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _code_blocks(text: str) -> list[str]:
    """Extract fenced code block bodies."""
    return re.findall(r"```(?:[A-Za-z0-9_+-]*)\n(.*?)```", text, re.DOTALL)


# ---------------------------------------------------------------------------
# Primitive graders
# ---------------------------------------------------------------------------


def required(substrings: Sequence[str]) -> Grader:
    """Fraction of required substrings present, case-insensitive."""
    needles = [s.lower() for s in substrings if s]
    if not needles:
        raise ValueError("required() needs at least one substring")

    def grade(text: str) -> float:
        low = text.lower()
        return sum(1 for n in needles if n in low) / len(needles)

    return grade


def forbidden(substrings: Sequence[str]) -> Grader:
    """1.0 when no forbidden substring appears, else 0.0."""
    needles = [s.lower() for s in substrings if s]
    if not needles:
        raise ValueError("forbidden() needs at least one substring")

    def grade(text: str) -> float:
        low = text.lower()
        return 0.0 if any(n in low for n in needles) else 1.0

    return grade


def matches(pattern: str) -> Grader:
    """1.0 when ``pattern`` matches anywhere in the response."""
    rx = re.compile(pattern, re.IGNORECASE | re.DOTALL)
    return lambda text: 1.0 if rx.search(text) else 0.0


def valid_json() -> Grader:
    """1.0 when the response, or its first code block, parses as JSON."""

    def grade(text: str) -> float:
        candidates = _code_blocks(text) or [text]
        for c in candidates:
            try:
                json.loads(c.strip())
                return 1.0
            except (json.JSONDecodeError, ValueError):
                continue
        return 0.0

    return grade


def parses_python() -> Grader:
    """1.0 when every Python code block parses.

    Syntactically invalid code is the single most common silent failure in
    generated output, and it is free to detect.
    """

    def grade(text: str) -> float:
        blocks = _code_blocks(text)
        if not blocks:
            return 0.0
        ok = 0
        for b in blocks:
            try:
                ast.parse(b)
                ok += 1
            except SyntaxError:
                pass
        return ok / len(blocks)

    return grade


def no_placeholders() -> Grader:
    """1.0 when the response contains no placeholder markers."""
    return forbidden(["TODO", "FIXME", "your code here", "implementation here", "..."])


def word_budget(maximum: int, *, minimum: int = 0) -> Grader:
    """1.0 inside the word range, degrading linearly outside it."""
    if maximum <= 0 or minimum < 0 or minimum > maximum:
        raise ValueError("require 0 <= minimum <= maximum and maximum > 0")

    def grade(text: str) -> float:
        n = len(text.split())
        if minimum <= n <= maximum:
            return 1.0
        if n < minimum:
            return _clamp(n / minimum) if minimum else 1.0
        return _clamp(maximum / n)

    return grade


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def composite(checks: Sequence[Check]) -> Grader:
    """Weighted mean of several checks."""
    if not checks:
        raise ValueError("composite() needs at least one check")
    total = sum(c.weight for c in checks)
    if total <= 0:
        raise ValueError("check weights must sum to a positive number")

    def grade(text: str) -> float:
        return _clamp(sum(c.fn(text) * c.weight for c in checks) / total)

    return grade


def build(
    *,
    require: Sequence[str] = (),
    forbid: Sequence[str] = (),
    regex: str | None = None,
    json_output: bool = False,
    python_code: bool = False,
    reject_placeholders: bool = False,
    max_words: int | None = None,
) -> Grader | None:
    """Assemble a grader from CLI-friendly flags.

    Returns ``None`` when no criteria were supplied, which callers must treat as
    "ungraded" rather than "everything passes".
    """
    checks: list[Check] = []
    if require:
        checks.append(Check("required", required(require), 2.0))
    if forbid:
        checks.append(Check("forbidden", forbidden(forbid), 2.0))
    if regex:
        checks.append(Check("regex", matches(regex), 1.5))
    if json_output:
        checks.append(Check("valid_json", valid_json(), 2.0))
    if python_code:
        checks.append(Check("parses_python", parses_python(), 2.0))
    if reject_placeholders:
        checks.append(Check("no_placeholders", no_placeholders(), 1.0))
    if max_words is not None:
        checks.append(Check("word_budget", word_budget(max_words), 1.0))

    if not checks:
        return None
    return composite(checks)


def is_degenerate(grader: Grader) -> bool:
    """True when a grader scores unrelated junk as a pass.

    A grader that cannot distinguish real output from noise will rank variants
    by cost alone, which is exactly how a quality regression ships.
    """
    probes = ["", "   ", "no", "lorem ipsum dolor sit amet", "?!?!"]
    try:
        return all(grader(p) >= 0.999 for p in probes)
    except Exception:
        return False
