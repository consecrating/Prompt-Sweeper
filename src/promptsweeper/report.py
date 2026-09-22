"""Terminal reporting. Plain ASCII, no dependencies.

One rule governs everything here: an estimate is never allowed to look like a
measurement. Estimated token counts carry a ``~`` and a label, and a sweep that
refused to pick a winner says so in the place where the winner would have been,
rather than leaving a blank the reader fills in optimistically.
"""

from __future__ import annotations

from collections.abc import Sequence

from .model import HAVE_OPUS5LEAN
from .sweep import SweepResult
from .variants import REDUNDANT_WITH_THINKING, Variant


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], indent: str = "  ") -> str:
    """Render an ASCII table, right-aligning numeric-looking columns."""
    cols = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(cols):
            if i < len(row):
                widths[i] = max(widths[i], len(str(row[i])))

    def numeric(i: int) -> bool:
        vals = [str(r[i]) for r in rows if i < len(r) and str(r[i]).strip()]
        if not vals:
            return False
        return all(
            v.lstrip("~$").rstrip("s%").replace(",", "").replace(".", "").isdigit() or v == "-"
            for v in vals
        )

    aligns = [numeric(i) for i in range(cols)]

    def fmt(cells: Sequence[str]) -> str:
        out = []
        for i in range(cols):
            cell = str(cells[i]) if i < len(cells) else ""
            out.append(cell.rjust(widths[i]) if aligns[i] else cell.ljust(widths[i]))
        return indent + "  ".join(out).rstrip()

    sep = indent + "  ".join("-" * w for w in widths)
    return "\n".join([fmt(headers), sep, *(fmt(r) for r in rows)])


def render_variants(variants: list[Variant], *, method: str, model: str) -> str:
    """Report generated variants and their cost, with no calls made."""
    mark = "" if method == "exact" else "~"
    base = next((v.tokens for v in variants if v.strategy == "baseline"), 0)

    rows = []
    for v in variants:
        delta = v.tokens - base
        sign = "+" if delta > 0 else ""
        flag = " (thinking covers this)" if v.strategy in REDUNDANT_WITH_THINKING else ""
        rows.append([
            v.strategy + flag,
            f"{mark}{v.tokens:,}",
            f"{sign}{delta:,}" if base and delta else "-",
            v.fixes,
        ])

    lines = [
        f"{len(variants)} variants  model={model}  tokens={method}",
        "",
        table(["strategy", "in tok", "vs base", "fixes"], rows),
        "",
    ]
    if method != "exact":
        lines.append("  tokens are estimated (typically within ~10-15%); --exact confirms, unbilled")
    if not HAVE_OPUS5LEAN:
        lines.append("  opus5lean not importable — using fallback price table; install Claude-Opus5")
    return "\n".join(lines)


def render_sweep(result: SweepResult, *, show_output: bool = False) -> str:
    """Report a completed sweep."""
    lines = [
        f"Sweep  model={result.model}  threshold={result.threshold:.2f}  "
        f"{'graded' if result.graded else 'UNGRADED'}",
        "",
    ]

    rows = []
    passing = {r.strategy for r in result.passing}
    for r in result.results:
        if not r.ok:
            rows.append([r.strategy, "-", "-", "-", "-", (r.errors[0][:40] if r.errors else "no trials")])
            continue
        verdict = "PASS" if r.strategy in passing else ("" if result.graded else "-")
        rows.append([
            r.strategy,
            f"{r.mean_score:.2f}" if result.graded else "-",
            f"{r.mean_output_tokens:,.0f}",
            f"${r.mean_cost:.4f}",
            f"{r.mean_latency:.1f}s",
            verdict,
        ])

    lines.append(table(["strategy", "score", "out tok", "cost/req", "latency", ""], rows))
    lines.append("")

    rec = result.recommended
    if rec:
        lines.append(f"  Recommended: {rec.strategy}")
        saving = result.savings_vs_baseline()
        base = result.baseline
        if saving is not None and base:
            direction = "cheaper" if saving > 0 else "more expensive"
            lines.append(
                f"  {abs(saving) * 100:.0f}% {direction} than baseline: "
                f"${rec.mean_cost:.4f} vs ${base.mean_cost:.4f} per request"
            )
        if rec.variant.wasteful_when:
            lines.append(f"  Caveat — wasteful when: {rec.variant.wasteful_when}")
    else:
        lines.append(f"  No recommendation: {result.refusal}")

    for note in result.notes:
        lines.append(f"  note: {note}")

    errored = [r for r in result.results if r.errors]
    if errored:
        lines.append("")
        lines.append("  Errors:")
        for r in errored:
            lines.append(f"    {r.strategy}: {r.errors[0][:110]}")

    if show_output and rec:
        lines += ["", "  Best output:", ""]
        for line in rec.best_text.splitlines():
            lines.append(f"    {line}")

    return "\n".join(lines)
