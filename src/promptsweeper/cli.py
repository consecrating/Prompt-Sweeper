"""Command-line interface.

    prompt-sweep generate TASK    show variants + token cost (offline, free)
    prompt-sweep best     TASK    print the winning prompt text, nothing else
    prompt-sweep run      TASK    execute every variant, score, recommend
    prompt-sweep recall   TASK    what won last time for this kind of task
    prompt-sweep stats            summarise the ledger
    prompt-sweep strategies       list strategies and what each fixes

Only ``run`` makes billed calls. Everything else is offline and needs no key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .model import DEFAULT_MODEL
from .report import render_sweep, render_variants, table
from .score import build as build_grader
from .store import Winner, classify, recall, record, stats
from .sweep import estimate, sweep
from .variants import REDUNDANT_WITH_THINKING, STRATEGIES


def _task(args: argparse.Namespace) -> str:
    """Read the task from an argument, a file, or stdin."""
    raw = args.task
    if raw == "-":
        return sys.stdin.read().strip()
    path = Path(raw)
    if args.file:
        if not path.is_file():
            raise SystemExit(f"error: no such file: {raw}")
        return path.read_text(encoding="utf-8").strip()
    return raw.strip()


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def _grader(args: argparse.Namespace):
    return build_grader(
        require=_csv(getattr(args, "require", None)) or (),
        forbid=_csv(getattr(args, "forbid", None)) or (),
        regex=getattr(args, "regex", None),
        json_output=getattr(args, "json_output", False),
        python_code=getattr(args, "python_code", False),
        reject_placeholders=getattr(args, "no_placeholders", False),
        max_words=getattr(args, "max_words", None),
    )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_generate(args: argparse.Namespace) -> int:
    variants, method = estimate(
        _task(args),
        model=args.model,
        only=_csv(args.only),
        exclude=_csv(args.exclude),
        exact=args.exact,
    )
    if args.json:
        print(json.dumps(
            {
                "method": method,
                "model": args.model,
                "variants": [
                    {
                        "strategy": v.strategy,
                        "tokens": v.tokens,
                        "fixes": v.fixes,
                        "wasteful_when": v.wasteful_when,
                        "prompt": v.prompt,
                    }
                    for v in variants
                ],
            },
            indent=2,
        ))
    else:
        print(render_variants(variants, method=method, model=args.model))
    return 0


def cmd_best(args: argparse.Namespace) -> int:
    """Print one prompt's text, ready to pipe. No decoration."""
    task = _task(args)
    strategy = args.strategy
    if not strategy:
        remembered = recall(task)
        if remembered:
            strategy = remembered["strategy"]
            print(
                f"# strategy={strategy} (recalled for {remembered['task_kind']}, "
                f"{remembered['observations']} observation(s))",
                file=sys.stderr,
            )
        else:
            strategy = "direct"
            print(
                f"# strategy={strategy} (no history for '{classify(task)}' tasks; "
                "run `prompt-sweep run` to establish one)",
                file=sys.stderr,
            )

    variants, _ = estimate(task, model=args.model, only=[strategy])
    if not variants:
        raise SystemExit(f"error: unknown strategy: {strategy}")
    sys.stdout.write(variants[0].prompt + "\n")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    task = _task(args)
    grader = _grader(args)

    def progress(strategy: str, i: int, total: int) -> None:
        if not args.quiet:
            print(f"  {strategy} ({i}/{total})", file=sys.stderr)

    result = sweep(
        task,
        model=args.model,
        grader=grader,
        threshold=args.threshold,
        trials=args.trials,
        only=_csv(args.only),
        exclude=_csv(args.exclude),
        system=args.system,
        effort=args.effort,
        max_tokens=args.max_tokens,
        require_all_trials=args.all_trials,
        skip_redundant=args.skip_redundant,
        on_progress=progress,
    )

    if args.json:
        print(json.dumps(
            {
                "task_kind": classify(task),
                "model": result.model,
                "graded": result.graded,
                "threshold": result.threshold,
                "recommended": result.recommended.strategy if result.recommended else None,
                "refusal": result.refusal,
                "savings_vs_baseline": result.savings_vs_baseline(),
                "notes": result.notes,
                "results": [
                    {
                        "strategy": r.strategy,
                        "mean_score": r.mean_score,
                        "worst_score": r.worst_score,
                        "mean_cost_usd": r.mean_cost,
                        "mean_latency_s": r.mean_latency,
                        "mean_output_tokens": r.mean_output_tokens,
                        "trials": len(r.trials),
                        "errors": r.errors,
                    }
                    for r in result.results
                ],
            },
            indent=2,
        ))
    else:
        print(render_sweep(result, show_output=args.show_output))

    rec = result.recommended
    if rec and args.save:
        winner = Winner(
            task_kind=classify(task),
            strategy=rec.strategy,
            model=result.model,
            score=rec.mean_score,
            cost_usd=rec.mean_cost,
            savings_vs_baseline=result.savings_vs_baseline(),
            trials=len(rec.trials),
            graded=result.graded,
            task_excerpt=task[:160],
        )
        info = record(winner, to_aibrain=not args.no_aibrain)
        if not args.json:
            print(f"\n  saved to {info['ledger']} ({info['entries']} entries)")
            if "aibrain" in info:
                print(f"  {info['aibrain']}")

    # Non-zero exit when the sweep could not justify a winner, so CI can gate.
    return 0 if rec else 1


def cmd_recall(args: argparse.Namespace) -> int:
    task = _task(args)
    found = recall(task)
    if args.json:
        print(json.dumps(found or {"task_kind": classify(task), "strategy": None}, indent=2))
        return 0 if found else 1
    if not found:
        print(f"No graded history for '{classify(task)}' tasks.")
        print("Run: prompt-sweep run \"<task>\" --python-code --save")
        return 1
    print(f"task kind:    {found['task_kind']}")
    print(f"best strategy: {found['strategy']}  (score {found['score']:.2f}, ${found['cost_usd']:.4f})")
    print(f"observations:  {found['observations']}")
    rows = [[k, str(v)] for k, v in found["strategy_counts"].items()]
    print()
    print(table(["strategy", "wins"], rows))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    data = stats()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"ledger:  {data['ledger']}")
    print(f"entries: {data['total']} ({data['graded']} graded)")
    print(f"aibrain: {data['aibrain'] or 'not installed'}")
    if data["by_task_kind"]:
        print()
        rows = []
        for kind, strategies in sorted(data["by_task_kind"].items()):
            for strat, n in sorted(strategies.items(), key=lambda kv: -kv[1]):
                rows.append([kind, strat, str(n)])
        print(table(["task kind", "strategy", "wins"], rows))
    return 0


def cmd_strategies(args: argparse.Namespace) -> int:
    rows = []
    for name, fn in STRATEGIES.items():
        v = fn("Write a function that parses a date string.")
        flag = " *" if name in REDUNDANT_WITH_THINKING else ""
        rows.append([name + flag, v.fixes, v.wasteful_when])
    if args.json:
        print(json.dumps(
            [
                {
                    "strategy": n,
                    "fixes": f("x y z").fixes,
                    "wasteful_when": f("x y z").wasteful_when,
                    "redundant_with_thinking": n in REDUNDANT_WITH_THINKING,
                }
                for n, f in STRATEGIES.items()
            ],
            indent=2,
        ))
        return 0
    print(table(["strategy", "fixes", "wasteful when"], rows))
    print()
    print("  * largely covered by Opus 5's default thinking; --skip-redundant drops these")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prompt-sweep",
        description="Generate, measure and pick prompts instead of guessing at them.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser, *, with_task: bool = True) -> None:
        if with_task:
            sp.add_argument("task", help="task text, a file path with --file, or '-' for stdin")
            sp.add_argument("-f", "--file", action="store_true", help="treat task as a file path")
        sp.add_argument("-m", "--model", default=DEFAULT_MODEL, help="model id for pricing/calls")
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    g = sub.add_parser("generate", help="show variants and token cost (offline, free)")
    add_common(g)
    g.add_argument("--only", help="comma-separated strategies to include")
    g.add_argument("--exclude", help="comma-separated strategies to skip")
    g.add_argument("--exact", action="store_true", help="exact token counts (unbilled, needs key)")
    g.set_defaults(func=cmd_generate)

    b = sub.add_parser("best", help="print the winning prompt text, ready to pipe")
    add_common(b)
    b.add_argument("--strategy", help="force a strategy instead of using recorded history")
    b.set_defaults(func=cmd_best)

    r = sub.add_parser("run", help="execute every variant, score, recommend (billed)")
    add_common(r)
    r.add_argument("--only", help="comma-separated strategies to include")
    r.add_argument("--exclude", help="comma-separated strategies to skip")
    r.add_argument("--skip-redundant", action="store_true", help="drop cot/self_check")
    r.add_argument("--trials", type=int, default=1, help="repeats per variant (default 1)")
    r.add_argument("--threshold", type=float, default=1.0, help="minimum passing score")
    r.add_argument("--all-trials", action="store_true", help="judge on the worst trial, not the mean")
    r.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="effort level")
    r.add_argument("--system", help="system prompt")
    r.add_argument("--max-tokens", type=int, default=4096, help="output cap per call")
    r.add_argument("--require", help="comma-separated substrings that must appear")
    r.add_argument("--forbid", help="comma-separated substrings that must not appear")
    r.add_argument("--regex", help="pattern that must match the response")
    r.add_argument("--json-output", action="store_true", help="grade: response must be valid JSON")
    r.add_argument("--python-code", action="store_true", help="grade: code blocks must parse")
    r.add_argument("--no-placeholders", action="store_true", help="grade: reject TODO/FIXME/...")
    r.add_argument("--max-words", type=int, help="grade: penalise responses longer than this")
    r.add_argument("--save", action="store_true", help="record the winner for future recall")
    r.add_argument("--no-aibrain", action="store_true", help="skip the AIBrain decision write")
    r.add_argument("--show-output", action="store_true", help="print the winning response")
    r.add_argument("-q", "--quiet", action="store_true", help="suppress progress lines")
    r.set_defaults(func=cmd_run)

    rc = sub.add_parser("recall", help="what won last time for this kind of task")
    add_common(rc)
    rc.set_defaults(func=cmd_recall)

    st = sub.add_parser("stats", help="summarise the ledger")
    add_common(st, with_task=False)
    st.set_defaults(func=cmd_stats)

    sg = sub.add_parser("strategies", help="list strategies and what each fixes")
    add_common(sg, with_task=False)
    sg.set_defaults(func=cmd_strategies)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
