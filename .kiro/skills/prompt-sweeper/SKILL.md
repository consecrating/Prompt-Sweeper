---
name: prompt-sweeper
description: Use when a prompt is underperforming, when the same kind of request keeps needing rework, when choosing between prompt phrasings, or when asked to improve, optimize, or shorten a prompt. Also apply before repeating a prompt at volume, where a per-request saving multiplies. Generates documented prompt variants, grades them, and recommends the cheapest one that holds quality.
metadata:
  version: "1.0"
  part-of: prompt-sweeper
---

# Prompt sweeping

Rewriting a prompt by feel is unmeasured A/B testing with one participant. You
try a few phrasings, keep whichever read best, and never learn what it cost. A
sweep replaces the feel with a number: N named variants, one grader, one winner.

Use this when the prompt is the variable. If the model, the context, or the task
definition is what is actually wrong, fix that first — a sweep will faithfully
find the best phrasing of a badly specified task.

## The rule that makes it trustworthy

**Pick the cheapest variant that clears the bar, not the highest-scoring one.**

Highest-score selection drifts toward the most verbose prompt, because
scaffolding usually scores marginally better while costing substantially more.
Over a prompt you send once, that is irrelevant. Over one you send 10,000 times,
it is the whole budget.

## Procedure

### 1. See the options before spending anything

```bash
prompt-sweep generate "<the task>"
```

Free and offline. Prints twelve variants with token deltas against the baseline,
what each strategy fixes, and — the part worth reading — **when each is a waste
of tokens**.

If the winner is obvious from the token table alone (for example, the task is
already terse and `minimal` saves nothing), stop. You have your answer without
an API call.

### 2. Define the bar *before* running

A sweep with no grader cannot pick a winner, and this tool refuses to pretend
otherwise. Decide what "correct" means in checkable terms:

```bash
--require "def,return"    # substrings that must appear
--forbid "TODO,FIXME"     # substrings that must not
--regex "def \w+\("       # a pattern that must match
--python-code             # every code block must parse (AST)
--json-output             # response must be valid JSON
--no-placeholders         # reject TODO / FIXME / ...
--max-words 300           # penalise runaway length
```

These check **shape, not correctness**. They are a first gate. When shape is not
your real bar, write a grader and call the library directly rather than stretching
a substring match into something it is not.

### 3. Run it

```bash
prompt-sweep run "<the task>" --python-code --no-placeholders \
  --trials 3 --skip-redundant --save
```

- `--trials 3` — one trial measures one sample of a stochastic process. Treat
  single-trial gaps as noise.
- `--all-trials` — judge on the *worst* trial, so a variant that only
  intermittently passes cannot win.
- `--skip-redundant` — drops `cot` and `self_check`. On Opus 5 thinking is on by
  default, so an explicit "reason step by step" mostly duplicates work you are
  billed for twice.
- `--save` — records the winner for later recall.

### 4. Reuse the finding

```bash
prompt-sweep recall "<a similar task>"     # what won for this kind of task
prompt-sweep best "<the task>"             # print that prompt, ready to pipe
prompt-sweep stats                         # everything learned so far
```

`best` writes the prompt to stdout and the chosen strategy to stderr, so it
pipes cleanly.

## Reading the result honestly

Three outcomes mean "no winner", and each is reported rather than papered over:

| Output | Meaning |
|---|---|
| `no grader supplied` | Cost and latency only. Choosing on cost alone ships regressions. |
| `grader is degenerate` | The grader passed empty and nonsense input, so it ranked nothing. |
| `no variant cleared threshold` | Everything failed the bar. Fix the task or lower the bar deliberately. |

`run` exits non-zero in all three cases, so CI can gate on it.

When a winner *is* reported, read two more lines before adopting it:

- the cost delta, which may be **negative** — a quality win that costs more is
  still reported as costing more
- the `wasteful when` caveat, which tells you the conditions under which this
  win will not transfer

## When not to sweep

- **One-off prompts.** The sweep costs more than the prompt saves.
- **The task is ambiguous.** Sweeping finds the best phrasing of a bad spec.
  Clarify first.
- **No checkable success criterion.** Without a grader there is nothing to
  optimise, and the tool will correctly refuse to guess.
- **The model is the problem.** See `model-profiles` for tier routing; a better
  prompt does not fix a mis-tiered model.

## Cost of the sweep itself

A sweep is `variants × trials` completions. Twelve variants at three trials is
36 calls — real money on Opus 5. Control it:

- `--only baseline,minimal,direct` to test a hypothesis rather than the field
- `--skip-redundant` to drop two strategies that rarely earn their place
- start at `--trials 1` to shortlist, then re-run the top two at `--trials 5`
- sweep on a cheaper model, then verify the winner on the expensive one — the
  ranking usually transfers even when absolute scores do not

## Integration

- `token-efficiency` governs the bytes each call returns; this skill governs
  what the prompt itself costs. They compose.
- **Claude-Opus5** supplies pricing and exact, unbilled token counts when
  installed. Without it a stdlib fallback keeps the offline path working.
- **AIBrain** receives `--save` results as decisions, so a finding lands with
  every other durable decision instead of in a silo.

## Reference

- `references/strategies.md` — all twelve strategies, what each fixes, and when
  each is a waste of tokens
- `references/grading.md` — building graders that can actually fail
