# prompt-sweeper

Stop guessing at prompts. Generate a fixed set of documented variations, run
them, score them, and keep the **cheapest** one that clears your quality bar.

Manual prompt engineering is unmeasured A/B testing with one participant. You try
four phrasings, keep whichever felt best, and never find out what it cost you.
This replaces "felt best" with a number.

```
$ prompt-sweep generate "Please could you write a Python function that parses an ISO date and handles errors"

  12 variants  model=claude-opus-5  tokens=estimate

    strategy                     in tok  vs base  fixes
    ---------------------------  ------  -------  ---------------------------------------------------
    baseline                         22        -  nothing — this is the control
    minimal                          17       -5  paying for politeness and hedging that carries no instruction
    direct                           38      +16  models spending output tokens restating the question
    output_contract                  74      +52  unparseable output when something downstream consumes it
    ...
```

## Install

No dependencies. The offline half works straight from a clone.

```bash
git clone https://github.com/consecrating/Prompt-Sweeper.git
cd Prompt-Sweeper
pip install -e .
prompt-sweep strategies
```

Python 3.10+.

## Do I need an API key?

| Command | Key needed | Cost |
|---|---|---|
| `generate`, `best`, `recall`, `stats`, `strategies` | No | Free, offline |
| `generate --exact` | Yes | **Free** — `count_tokens` is unbilled |
| `run` | Yes, with credit | Real completions |

Only `run` spends money. Everything else is stdlib and offline.

## The problem this solves

You cannot tell whether a prompt is good. You can only tell whether it is
*better than another prompt on a task you can grade*. So the unit of work here is
a sweep: N variants, one grader, one winner.

Selection is **cheapest-passing, not highest-scoring.** Highest-score selection
quietly pushes you toward the most verbose prompt, because scaffolding tends to
score marginally better while costing substantially more. Cheapest-passing keeps
the win honest.

## The five commands

### `generate` — what are my options, and what do they cost?

```bash
prompt-sweep generate "Write a retry decorator with exponential backoff"
prompt-sweep generate task.md --file --exact
```

Twelve strategies, each stating what it fixes and — importantly — **when it is a
waste of tokens**:

| Strategy | Fixes |
|---|---|
| `baseline` | nothing; it is the control |
| `minimal` | paying for politeness and hedging |
| `direct` | output spent restating the question |
| `output_contract` | unparseable output |
| `negative` | known recurring failure modes |
| `spec` | silently dropping half a multi-part request |
| `checklist` | out-of-order work that breaks dependencies |
| `decompose` | large tasks answered shallowly |
| `role` | toy-quality output with no error handling |
| `self_check` \* | confidently wrong first drafts |
| `cot` \* | logic answered by pattern-matching |
| `few_shot` | right answer, wrong shape |

\* Largely absorbed by Opus 5's default thinking. `--skip-redundant` drops them,
and reports that it did.

### `run` — which one actually wins?

Needs credit; makes real calls.

```bash
prompt-sweep run "Write a retry decorator with exponential backoff" \
  --python-code --no-placeholders --trials 3 --skip-redundant --save
```

```
  Sweep  model=claude-opus-5  threshold=1.00  graded

    strategy         score  out tok  cost/req  latency
    ---------------  -----  -------  --------  -------
    baseline          0.50      412   $0.0113     3.2s
    minimal           1.00      388   $0.0104     3.1s  PASS
    direct            1.00      351   $0.0098     2.9s  PASS
    spec              1.00    1,190   $0.0312     7.9s  PASS

    Recommended: direct
    13% cheaper than baseline: $0.0098 vs $0.0113 per request
    Caveat — wasteful when: the model is already terse, or you want the reasoning shown
```

`--save` records the winner so the next sweep starts from what worked.

### `best` — give me the prompt, nothing else

Pipe-friendly. Uses recorded history for this kind of task when it has any.

```bash
prompt-sweep best "Write a retry decorator" | pbcopy
prompt-sweep best "Fix this traceback" --strategy checklist
```

The strategy chosen is announced on **stderr**, so stdout stays a clean prompt.

### `recall` / `stats` — what have I learned?

```bash
prompt-sweep recall "Write a function to validate emails"
prompt-sweep stats
```

Tasks are bucketed with coarse keyword matching (`code-generation`, `debugging`,
`refactor`, `test`, …). The buckets are deliberately crude and inspectable — a
cheap classifier you can read and disagree with beats an opaque one you cannot.

## Grading

A sweep is only as good as its grader, so the graders are deterministic and free:

```bash
--require "def,return"      # substrings that must appear
--forbid "TODO,FIXME"       # substrings that must not
--regex "def \w+\("         # pattern that must match
--python-code               # every code block must parse (ast)
--json-output               # response must be valid JSON
--no-placeholders           # reject TODO / FIXME / ...
--max-words 300             # penalise runaway length
```

Three refusals are built in, because each is a way this tool could otherwise
produce a confident lie:

| Situation | Behaviour |
|---|---|
| No grader given | Reports cost and latency, **recommends nothing** |
| Grader passes empty/nonsense input | Flags it as degenerate, **recommends nothing** |
| Nothing clears the threshold | Reports the gap, **recommends nothing** |

`run` exits non-zero when it cannot justify a winner, so CI can gate on it.

Use `--trials 3 --all-trials` to judge on the *worst* trial instead of the mean,
so a variant that passes only intermittently cannot win.

## Library use

```python
from promptsweeper import generate, sweep, score, recall

for v in generate("Write a retry decorator"):
    print(v.strategy, v.tokens, v.fixes)

grader = score.build(require=["def"], python_code=True, reject_placeholders=True)
result = sweep("Write a retry decorator", grader=grader, trials=3)

if result.recommended:
    print(result.recommended.strategy, result.savings_vs_baseline())
else:
    print("no winner:", result.refusal)
```

## How it fits the rest of the stack

- **Claude-Opus5** — when `opus5lean` is importable, pricing, exact token counts
  and the Messages client are delegated to it. A second copy of a price table is
  a second thing to go stale. Without it, a stdlib fallback keeps everything
  offline working.
- **AIBrain** — `--save` writes the winning strategy into AIBrain's decision log,
  so the finding lands where every other durable decision lives instead of in a
  silo. Missing AIBrain is reported, never fatal.
- **SuperBrain** — cloned, installed and verified by `scripts/bootstrap.sh`.

## Development

```bash
pip install -e '.[dev]'
pytest          # offline, no key, no network
ruff check .
```

Tests are offline by construction: the model client is faked, and the ledger is
redirected to a tmp directory.

## Caveats

- The offline token estimator is a heuristic, typically within ~10-15%. Use
  `--exact` for anything you will act on; it is unbilled.
- Substring and AST graders check *shape*, not correctness. They are a first
  gate, not an eval suite. For real quality bars, pass your own grader.
- `--trials 1` measures one sample of a stochastic process. Treat small
  differences as noise until you raise the trial count.
- Task-kind buckets are keyword-based and will occasionally mis-file a task.
  `recall` prints the bucket it used so you can override with `--strategy`.

## License

MIT
