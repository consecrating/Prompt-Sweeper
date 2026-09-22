"""Winning-pattern store.

A sweep you run once and forget is an experiment. A sweep whose result you keep
is a policy. This module records which strategy won for which kind of task, so
that later sweeps can start from what already worked instead of rediscovering it.

Two sinks:

* a local JSON ledger, always available,
* AIBrain's decision log, when that repo is present, so the finding lands in the
  same place as every other durable decision rather than in a silo.

Task kinds are derived from coarse keyword buckets. This is deliberately crude:
a cheap, inspectable bucket that is occasionally wrong is more useful here than
an opaque classifier, because the user can read the bucket and disagree with it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

STORE_ENV = "PROMPTSWEEPER_STORE"
DEFAULT_STORE = Path.home() / ".promptsweeper" / "winners.json"
AIBRAIN_PATHS = (
    Path("/projects/sandbox/AIBrain"),
    Path.home() / "AIBrain",
)

_BUCKETS: dict[str, tuple[str, ...]] = {
    "code-generation": ("function", "implement", "write a", "class", "script", "module"),
    "debugging": ("debug", "error", "traceback", "failing", "broken", "crash", "bug"),
    "refactor": ("refactor", "clean up", "simplify", "rename", "extract", "restructure"),
    "review": ("review", "audit", "critique", "assess", "inspect"),
    "explain": ("explain", "why", "how does", "what is", "describe", "summarise", "summarize"),
    "data-extraction": ("extract", "parse", "scrape", "json", "csv", "convert"),
    "test": ("test", "pytest", "unit test", "coverage", "assert"),
    "docs": ("document", "readme", "docstring", "changelog", "guide"),
    "design": ("design", "layout", "ui", "ux", "css", "component", "theme"),
    "sql": ("sql", "query", "select ", "join", "schema", "migration"),
}


def classify(task: str) -> str:
    """Bucket a task description. Returns ``"general"`` when nothing matches."""
    low = task.lower()
    best, best_hits = "general", 0
    for kind, needles in _BUCKETS.items():
        hits = sum(1 for n in needles if n in low)
        if hits > best_hits:
            best, best_hits = kind, hits
    return best


@dataclass
class Winner:
    """One recorded sweep outcome."""

    task_kind: str
    strategy: str
    model: str
    score: float
    cost_usd: float
    savings_vs_baseline: float | None
    trials: int
    graded: bool
    task_excerpt: str
    recorded_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))


def store_path() -> Path:
    """Resolve the ledger path, honouring ``PROMPTSWEEPER_STORE``."""
    override = os.environ.get(STORE_ENV)
    return Path(override) if override else DEFAULT_STORE


def load() -> list[dict]:
    """Read the ledger. Returns ``[]`` when absent or corrupt."""
    path = store_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save(entries: list[dict]) -> Path:
    """Write the ledger, creating parent directories as needed."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return path


def find_aibrain() -> Path | None:
    """Locate an AIBrain checkout with a usable ``brain.sh``.

    ``AIBRAIN_ROOT`` is authoritative when set: an explicit override that
    silently falls back to a default search path is not an override.
    """
    env = os.environ.get("AIBRAIN_ROOT")
    candidates = [Path(env)] if env else list(AIBRAIN_PATHS)
    for path in candidates:
        if (path / "scripts" / "brain.sh").is_file():
            return path
    return None


def record_to_aibrain(winner: Winner) -> str | None:
    """Append the finding to AIBrain's decision log.

    Returns a status string, or ``None`` when AIBrain is not installed. Failures
    are reported rather than raised: losing a bookkeeping write must not fail a
    sweep that already cost real money.
    """
    root = find_aibrain()
    if not root:
        return None

    saved = ""
    if winner.savings_vs_baseline is not None:
        saved = f", {winner.savings_vs_baseline * 100:.0f}% cheaper than baseline"
    decision = f"For {winner.task_kind} tasks, use the '{winner.strategy}' prompt strategy"
    reason = (
        f"Swept {winner.trials} trial(s) on {winner.model}: "
        f"score {winner.score:.2f}, ${winner.cost_usd:.4f}/request{saved}"
    )

    try:
        proc = subprocess.run(
            [str(root / "scripts" / "brain.sh"), "decide", decision, reason],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"AIBrain write failed: {exc}"
    if proc.returncode != 0:
        return f"AIBrain write failed: {(proc.stderr or proc.stdout).strip()[:200]}"
    return f"recorded in AIBrain at {root}"


def record(winner: Winner, *, to_aibrain: bool = True) -> dict:
    """Persist a winner locally and, optionally, to AIBrain."""
    entries = load()
    entries.append(asdict(winner))
    path = save(entries)
    out = {"ledger": str(path), "entries": len(entries)}
    if to_aibrain:
        status = record_to_aibrain(winner)
        out["aibrain"] = status or "AIBrain not installed — skipped"
    return out


def recall(task: str) -> dict | None:
    """Return the best previously recorded strategy for this kind of task.

    "Best" is the highest score, tie-broken on cost, among graded entries for
    the matching bucket. Ungraded entries are ignored on purpose: they were
    never evidence of quality in the first place.
    """
    kind = classify(task)
    entries = [
        e for e in load() if e.get("task_kind") == kind and e.get("graded")
    ]
    if not entries:
        return None
    best = sorted(
        entries,
        key=lambda e: (-float(e.get("score", 0)), float(e.get("cost_usd", 0))),
    )[0]
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["strategy"]] = counts.get(e["strategy"], 0) + 1
    return {
        "task_kind": kind,
        "strategy": best.get("strategy"),
        "score": best.get("score"),
        "cost_usd": best.get("cost_usd"),
        "recorded_at": best.get("recorded_at"),
        "observations": len(entries),
        "strategy_counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }


def stats() -> dict:
    """Summarise the ledger by task kind and strategy."""
    entries = load()
    by_kind: dict[str, dict[str, int]] = {}
    for e in entries:
        kind = e.get("task_kind", "general")
        strat = e.get("strategy", "unknown")
        by_kind.setdefault(kind, {})
        by_kind[kind][strat] = by_kind[kind].get(strat, 0) + 1
    return {
        "ledger": str(store_path()),
        "total": len(entries),
        "graded": sum(1 for e in entries if e.get("graded")),
        "by_task_kind": by_kind,
        "aibrain": str(find_aibrain()) if find_aibrain() else None,
    }
