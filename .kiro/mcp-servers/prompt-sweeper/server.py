#!/usr/bin/env python3
"""MCP server exposing prompt-sweeper over stdio.

Four tools. Three are offline and free; only ``prompt_sweep_run`` makes billed
calls, and it says so in its description so a calling agent can weigh that.

    python server.py --stdio
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    from promptsweeper import score, store
    from promptsweeper.sweep import estimate, sweep
    from promptsweeper.variants import REDUNDANT_WITH_THINKING, STRATEGIES
except ImportError:  # pragma: no cover
    print(
        json.dumps({
            "error": "promptsweeper is not importable. Run: pip install -e /projects/sandbox/Prompt-Sweeper"
        }),
        file=sys.stderr,
    )
    raise


def tool_generate(params: dict) -> dict:
    """Generate variants with token costs. Offline, free."""
    task = params.get("task", "")
    if not task.strip():
        return {"error": "task is required"}
    try:
        variants, method = estimate(
            task,
            model=params.get("model", "claude-opus-5"),
            only=params.get("only"),
            exclude=params.get("exclude"),
        )
    except ValueError as exc:
        return {"error": str(exc)}

    baseline = next((v.tokens for v in variants if v.strategy == "baseline"), 0)
    return {
        "task_kind": store.classify(task),
        "token_method": method,
        "variants": [
            {
                "strategy": v.strategy,
                "tokens": v.tokens,
                "delta_vs_baseline": v.tokens - baseline,
                "fixes": v.fixes,
                "wasteful_when": v.wasteful_when,
                "redundant_with_thinking": v.strategy in REDUNDANT_WITH_THINKING,
                "prompt": v.prompt,
            }
            for v in variants
        ],
    }


def tool_run(params: dict) -> dict:
    """Execute every variant, grade, recommend. Makes billed API calls."""
    task = params.get("task", "")
    if not task.strip():
        return {"error": "task is required"}

    grader = score.build(
        require=params.get("require") or (),
        forbid=params.get("forbid") or (),
        regex=params.get("regex"),
        json_output=bool(params.get("json_output")),
        python_code=bool(params.get("python_code")),
        reject_placeholders=bool(params.get("no_placeholders")),
        max_words=params.get("max_words"),
    )

    try:
        result = sweep(
            task,
            model=params.get("model", "claude-opus-5"),
            grader=grader,
            threshold=float(params.get("threshold", 1.0)),
            trials=int(params.get("trials", 1)),
            only=params.get("only"),
            exclude=params.get("exclude"),
            effort=params.get("effort"),
            require_all_trials=bool(params.get("all_trials")),
            skip_redundant=bool(params.get("skip_redundant")),
        )
    except ValueError as exc:
        return {"error": str(exc)}

    rec = result.recommended
    out = {
        "task_kind": store.classify(task),
        "graded": result.graded,
        "threshold": result.threshold,
        "recommended": rec.strategy if rec else None,
        "recommended_prompt": rec.variant.prompt if rec else None,
        "refusal": result.refusal,
        "savings_vs_baseline": result.savings_vs_baseline(),
        "notes": result.notes,
        "results": [
            {
                "strategy": r.strategy,
                "mean_score": round(r.mean_score, 4),
                "worst_score": round(r.worst_score, 4),
                "mean_cost_usd": round(r.mean_cost, 6),
                "mean_latency_s": round(r.mean_latency, 3),
                "trials": len(r.trials),
                "errors": r.errors,
            }
            for r in result.results
        ],
    }

    if rec and params.get("save"):
        winner = store.Winner(
            task_kind=store.classify(task),
            strategy=rec.strategy,
            model=result.model,
            score=rec.mean_score,
            cost_usd=rec.mean_cost,
            savings_vs_baseline=result.savings_vs_baseline(),
            trials=len(rec.trials),
            graded=result.graded,
            task_excerpt=task[:160],
        )
        out["saved"] = store.record(winner, to_aibrain=not params.get("no_aibrain"))
    return out


def tool_recall(params: dict) -> dict:
    """What won previously for this kind of task. Offline, free."""
    task = params.get("task", "")
    if not task.strip():
        return {"error": "task is required"}
    found = store.recall(task)
    if not found:
        return {"task_kind": store.classify(task), "strategy": None,
                "note": "no graded history; run prompt_sweep_run with save=true"}
    return found


def tool_strategies(params: dict) -> dict:
    """List strategies, what each fixes, and when each wastes tokens. Free."""
    out = []
    for name, fn in STRATEGIES.items():
        v = fn("placeholder task text")
        out.append({
            "strategy": name,
            "fixes": v.fixes,
            "wasteful_when": v.wasteful_when,
            "redundant_with_thinking": name in REDUNDANT_WITH_THINKING,
        })
    return {"strategies": out}


TOOLS = {
    "prompt_sweep_generate": tool_generate,
    "prompt_sweep_run": tool_run,
    "prompt_sweep_recall": tool_recall,
    "prompt_sweep_strategies": tool_strategies,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="prompt-sweeper MCP server")
    parser.add_argument("--stdio", action="store_true", help="use stdio transport")
    args = parser.parse_args()

    if not args.stdio:
        print("only --stdio is implemented", file=sys.stderr)
        return 1

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": {"code": -32700, "message": f"parse error: {exc}"}}), flush=True)
            continue

        tool = str(msg.get("method", "")).split("/")[-1]
        handler = TOOLS.get(tool)
        if not handler:
            print(json.dumps({
                "id": msg.get("id"),
                "error": {"code": -32601, "message": f"unknown tool: {tool}"},
            }), flush=True)
            continue

        try:
            result = handler(msg.get("params") or {})
            print(json.dumps({"id": msg.get("id"), "result": result}), flush=True)
        except Exception as exc:  # a tool fault must not kill the server
            print(json.dumps({
                "id": msg.get("id"),
                "error": {"code": -32603, "message": str(exc)},
            }), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
