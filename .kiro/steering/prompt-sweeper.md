---
inclusion: fileMatch
fileMatchPattern: ".kiro/skills/**/*.md|.kiro/steering/**/*.md|**/prompts/**|**/*prompt*.md"
description: "Invariants to preserve when editing prompt text or prompt-sweeper itself."
---

# Editing prompts and prompt-sweeper

## When editing prompt text in this workspace

Before rewriting a prompt by hand, check whether a sweep already answered the
question:

```bash
prompt-sweep recall "<the task>"
```

If a strategy is recorded for that kind of task, start from it. Overriding a
recorded winner by intuition is how the measurement gets thrown away.

After a substantive prompt rewrite, verify the direction of the change:

```bash
prompt-sweep generate "<old>" --json | jq '.variants[0].tokens'
prompt-sweep generate "<new>" --json | jq '.variants[0].tokens'
```

A rewrite that grew the prompt should be justified by a graded result, not by it
reading better.

## Invariants when editing this package

These are load-bearing. Breaking any of them makes the tool confidently wrong,
which is worse than not having it.

1. **An estimate is never presented as a measurement.** Estimated token counts
   carry `~` and a labelled method. `CountResult.method` must survive every hop
   from measurement to report.

2. **No grader means no recommendation.** `score.build()` returns `None` when
   given no criteria, and `SweepResult.recommended` must stay `None` for an
   ungraded sweep. Never default to a grader that always returns 1.0.

3. **Degenerate graders are reported, not tolerated.** Keep the
   `is_degenerate` probe wired into `sweep`.

4. **Selection is cheapest-passing.** Not highest-scoring. Changing this silently
   biases every recommendation toward verbosity.

5. **Cost deltas are signed.** When the winner costs more than baseline, the
   report says so. Never take an absolute value there.

6. **Every strategy declares `wasteful_when`.** A strategy that cannot say when
   it fails cannot be reasoned about. The test suite enforces this.

7. **Offline stays offline.** `generate`, `best`, `recall`, `stats` and
   `strategies` make no network calls. Only `run`, and `generate --exact`, touch
   the API — and `--exact` hits the unbilled endpoint.

8. **One price table.** Pricing is delegated to `opus5lean` when importable. The
   fallback table in `model.py` exists only for when it is not, and must stay
   clearly marked as such.

9. **Bookkeeping failures are not fatal.** A failed AIBrain write is reported and
   the sweep result is still returned. Losing a ledger entry must never discard
   calls the user already paid for.

10. **The model call is injected, not monkeypatched.** `promptsweeper.sweep` the
    function shadows the module of the same name, so patching a module global is
    quietly ineffective. Keep `complete_fn` as the seam.

## Testing

Tests are offline by construction — the model call is stubbed via `complete_fn`
and the ledger is redirected with `PROMPTSWEEPER_STORE`. A test that needs a
network call or an API key does not belong in this suite.

```bash
pytest && ruff check .
```
