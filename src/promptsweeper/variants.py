"""Prompt variant generation — the actual intellectual property of this package.

Manual prompt engineering is a person trying four phrasings, keeping whichever
felt best, and never measuring. This module replaces "felt best" with a fixed
set of named, documented transformations, so that what gets compared is a
*strategy* rather than a mood.

Every strategy states:

* what it changes about the prompt,
* which failure mode it is supposed to fix,
* when it is expected to be a waste of tokens.

That last field matters more than it looks. A strategy that cannot describe when
it fails is a strategy you cannot reason about, and a sweep full of those is
just noise with a leaderboard attached.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Variant:
    """One candidate prompt, plus why it exists.

    Attributes:
        strategy: Stable identifier, e.g. ``"output_contract"``.
        prompt: The full prompt text to send.
        fixes: The failure mode this transformation targets.
        wasteful_when: Conditions under which this strategy costs tokens for
            no benefit. Used by the reporter to flag likely-redundant variants.
        tokens: Estimated token count, filled in by the sweeper.
    """

    strategy: str
    prompt: str
    fixes: str
    wasteful_when: str
    tokens: int = 0

    def with_tokens(self, n: int) -> Variant:
        return Variant(
            strategy=self.strategy,
            prompt=self.prompt,
            fixes=self.fixes,
            wasteful_when=self.wasteful_when,
            tokens=n,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sentences(task: str) -> list[str]:
    """Split a task into sentence-ish chunks, keeping code spans intact."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", task.strip())
    return [p.strip() for p in parts if p.strip()]


def _imperatives(task: str) -> list[str]:
    """Extract requirement-looking clauses from a free-text task."""
    out: list[str] = []
    for chunk in re.split(r"[.;\n]|\band\b|\balso\b", task):
        chunk = chunk.strip(" ,\t-–—")
        if len(chunk.split()) >= 3:
            out.append(chunk[0].upper() + chunk[1:])
    return out or [task.strip()]


def _strip_filler(task: str) -> str:
    """Remove politeness and hedging that carries no instruction."""
    filler = [
        r"\bplease\b", r"\bcould you\b", r"\bcan you\b", r"\bi(?:'d| would) like\b",
        r"\bi need you to\b", r"\bi want you to\b", r"\bif possible\b",
        r"\bkindly\b", r"\bjust\b", r"\bmaybe\b", r"\bperhaps\b", r"\bfor me\b",
        r"\bthanks?(?: you)?\b", r"\bwould be great\b", r"\bhelp me\b",
    ]
    out = task
    for pat in filler:
        out = re.sub(pat, "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    return out.strip(" ,.\t\n").strip()


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def s_baseline(task: str) -> Variant:
    return Variant(
        strategy="baseline",
        prompt=task,
        fixes="nothing — this is the control",
        wasteful_when="never; you need a control to measure against",
    )


def s_minimal(task: str) -> Variant:
    core = _strip_filler(task)
    return Variant(
        strategy="minimal",
        prompt=core,
        fixes="paying for politeness and hedging that carries no instruction",
        wasteful_when="the task was already terse; savings will round to zero",
    )


def s_direct(task: str) -> Variant:
    core = _strip_filler(task)
    return Variant(
        strategy="direct",
        prompt=f"{core}\n\nAnswer directly. No preamble, no restatement of the task, no closing summary.",
        fixes="models spending output tokens restating the question before answering",
        wasteful_when="the model is already terse, or you want the reasoning shown",
    )


def s_spec(task: str) -> Variant:
    reqs = _imperatives(task)
    body = "\n".join(f"R{i}. {r}" for i, r in enumerate(reqs, 1))
    checks = "\n".join(f"- [ ] R{i} is satisfied" for i in range(1, len(reqs) + 1))
    return Variant(
        strategy="spec",
        prompt=(
            "Implement the following requirements.\n\n"
            f"Requirements:\n{body}\n\n"
            f"Acceptance criteria — every box must be checkable against your output:\n{checks}\n\n"
            "If a requirement is ambiguous, state the interpretation you chose and continue."
        ),
        fixes="silently dropping half of a multi-part request",
        wasteful_when="single-requirement tasks; the scaffolding outweighs the task",
    )


def s_checklist(task: str) -> Variant:
    steps = _imperatives(task)
    body = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return Variant(
        strategy="checklist",
        prompt=(
            f"{_strip_filler(task)}\n\n"
            f"Work through these in order:\n{body}\n\n"
            "Complete each step before moving to the next."
        ),
        fixes="out-of-order work that breaks dependencies between steps",
        wasteful_when="the task has no ordering constraint",
    )


def s_role(task: str) -> Variant:
    return Variant(
        strategy="role",
        prompt=(
            "You are a senior engineer reviewing this for a production codebase "
            "that other people maintain.\n\n"
            f"{_strip_filler(task)}\n\n"
            "Hold it to the standard you would apply in code review."
        ),
        fixes="toy-quality output: no error handling, no edge cases, no types",
        wasteful_when="the model is already strong; persona prompts are largely "
        "cosmetic on frontier models and cost real tokens",
    )


def s_negative(task: str) -> Variant:
    return Variant(
        strategy="negative",
        prompt=(
            f"{_strip_filler(task)}\n\n"
            "Do not:\n"
            "- include placeholder comments such as TODO, FIXME, or `...`\n"
            "- omit error handling for inputs that can realistically fail\n"
            "- explain what you are about to do before doing it\n"
            "- add dependencies beyond what the task requires"
        ),
        fixes="known recurring failure modes, stated as prohibitions",
        wasteful_when="the listed failures were never happening for this task",
    )


def s_output_contract(task: str) -> Variant:
    return Variant(
        strategy="output_contract",
        prompt=(
            f"{_strip_filler(task)}\n\n"
            "Output format — follow exactly:\n"
            "1. The deliverable itself, in a single fenced code block if it is code.\n"
            "2. Nothing before it.\n"
            "3. After it, at most three bullet points noting assumptions you made.\n"
            "Do not include any other sections."
        ),
        fixes="unparseable output when something downstream has to consume it",
        wasteful_when="a human reads the output directly and format does not matter",
    )


def s_few_shot(task: str) -> Variant:
    return Variant(
        strategy="few_shot",
        prompt=(
            "Match the shape and level of detail of this example.\n\n"
            "Example task: validate an email address\n"
            "Example response:\n"
            "```python\n"
            "import re\n\n"
            "_EMAIL = re.compile(r\"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$\")\n\n\n"
            "def is_email(value: str) -> bool:\n"
            "    \"\"\"Return True if value looks like an email address.\"\"\"\n"
            "    if not isinstance(value, str):\n"
            "        raise TypeError(\"value must be a string\")\n"
            "    return bool(_EMAIL.match(value))\n"
            "```\n"
            "- Assumes validation is structural, not deliverability\n\n"
            f"Now the real task: {_strip_filler(task)}"
        ),
        fixes="output that is correct but formatted or scoped unlike what you wanted",
        wasteful_when="the example is long relative to the task — it is pure input cost",
    )


def s_decompose(task: str) -> Variant:
    return Variant(
        strategy="decompose",
        prompt=(
            f"{_strip_filler(task)}\n\n"
            "First, list the sub-problems this breaks into, one line each. "
            "Then solve each one in that order. Keep the list to at most five items."
        ),
        fixes="large tasks answered shallowly because they were treated as one step",
        wasteful_when="the task is already atomic; you pay for a list of one item",
    )


def s_self_check(task: str) -> Variant:
    return Variant(
        strategy="self_check",
        prompt=(
            f"{_strip_filler(task)}\n\n"
            "Before you finish: re-read your output against the task and fix anything "
            "that does not satisfy it. Then state in one line what you verified. "
            "Do not show the intermediate draft."
        ),
        fixes="confidently wrong first drafts shipped without review",
        wasteful_when="thinking is already enabled — the model self-checks internally, "
        "so this duplicates work you are billed for twice",
    )


def s_cot(task: str) -> Variant:
    return Variant(
        strategy="cot",
        prompt=(
            f"{_strip_filler(task)}\n\n"
            "Reason step by step before giving the final answer. "
            "Keep the reasoning under 100 words."
        ),
        fixes="arithmetic and multi-constraint logic answered by pattern-matching",
        wasteful_when="on Claude Opus 5 thinking is on by default, so an explicit "
        "chain-of-thought instruction usually buys nothing and bills as output",
    )


#: Registry, in a deliberate order: control first, then cheap wins, then
#: scaffolding-heavy strategies. The sweeper preserves this order so that
#: "cheapest passing variant" resolves predictably.
STRATEGIES: dict[str, Callable[[str], Variant]] = {
    "baseline": s_baseline,
    "minimal": s_minimal,
    "direct": s_direct,
    "output_contract": s_output_contract,
    "negative": s_negative,
    "spec": s_spec,
    "checklist": s_checklist,
    "decompose": s_decompose,
    "role": s_role,
    "self_check": s_self_check,
    "cot": s_cot,
    "few_shot": s_few_shot,
}

#: Strategies whose stated benefit is largely absorbed by Opus 5's default
#: thinking. Reported as such rather than silently dropped.
REDUNDANT_WITH_THINKING = frozenset({"cot", "self_check"})


def generate(
    task: str,
    *,
    only: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
) -> list[Variant]:
    """Build prompt variants for ``task``.

    Args:
        only: Restrict to these strategy names, in registry order.
        exclude: Skip these strategy names.

    Raises:
        ValueError: If ``task`` is empty or a named strategy does not exist.
    """
    if not task or not task.strip():
        raise ValueError("task must be a non-empty string")

    names = list(STRATEGIES)
    if only:
        unknown = [n for n in only if n not in STRATEGIES]
        if unknown:
            raise ValueError(f"unknown strategies: {', '.join(unknown)}")
        names = [n for n in names if n in set(only)]
    if exclude:
        unknown = [n for n in exclude if n not in STRATEGIES]
        if unknown:
            raise ValueError(f"unknown strategies: {', '.join(unknown)}")
        names = [n for n in names if n not in set(exclude)]

    return [STRATEGIES[n](task) for n in names]
