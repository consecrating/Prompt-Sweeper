# Grading: building a bar that can actually fail

A sweep is only as good as its grader. A grader that cannot fail turns the whole
exercise into "pick the cheapest prompt", which is precisely how a quality
regression ships with a chart attached.

## The one test every grader must pass

Score these five inputs. If they all pass, the grader is measuring nothing:

```
""            (empty)
"   "         (whitespace)
"no"          (minimal refusal)
"lorem ipsum dolor sit amet"
"?!?!"
```

`promptsweeper.score.is_degenerate` runs exactly this check, and `run` refuses to
recommend a winner when it trips. If your grader fails this test, the fix is a
stricter grader, not a lower threshold.

## What the built-in checks actually verify

| Flag | Verifies | Does **not** verify |
|---|---|---|
| `--require` | named substrings present | that they are used correctly |
| `--forbid` | named substrings absent | absence of equivalent problems |
| `--regex` | a pattern matches | semantics of the match |
| `--python-code` | every code block parses | that the code is correct |
| `--json-output` | response parses as JSON | schema conformance |
| `--no-placeholders` | no TODO / FIXME / `...` | completeness of the logic |
| `--max-words` | length within budget | that brevity kept the substance |

Every one of these is a **shape** check. Shape is worth checking because it is
free and catches the loudest failures — syntactically invalid code is the most
common silent defect in generated output. But shape is not correctness, and a
sweep that only checks shape should be described that way.

## Weighting

`score.build` assigns weights so that structural failures dominate cosmetic ones:

| Check | Weight | Why |
|---|---|---|
| `required`, `forbidden` | 2.0 | the explicit contract |
| `valid_json`, `parses_python` | 2.0 | unparseable output is unusable |
| `regex` | 1.5 | usually a structural claim |
| `no_placeholders`, `word_budget` | 1.0 | quality signals, not hard failures |

Weighted mean, clamped to [0, 1].

## Thresholds

`--threshold` is the minimum passing score.

- **1.0** — everything must pass. Correct for hard contracts (must be valid
  JSON, must parse). Start here.
- **0.8** — tolerate one soft failure out of several checks. Reasonable when
  `--max-words` is in the mix and you care more about correctness than length.
- **below 0.5** — you are no longer measuring quality. If nothing clears 0.5,
  the task or the grader is wrong; lowering the bar only hides that.

## Trials, and why one is not enough

Generation is stochastic. One trial per variant measures one sample, and ranking
twelve variants on one sample each mostly ranks noise.

| Trials | Use |
|---|---|
| 1 | shortlisting a wide field cheaply |
| 3 | the default for a decision you will act on |
| 5+ | variants separated by a few percent |

`--all-trials` switches judgement from the mean to the **worst** trial. Use it
whenever intermittent failure is unacceptable: a variant that passes two times in
three has a 33% defect rate, and a mean of 0.67 flatters it.

## Custom graders

When shape checks cannot express your bar, write a function. Anything
`str -> float` in [0, 1] works:

```python
from promptsweeper import sweep
from promptsweeper.score import Check, composite, parses_python

def imports_stdlib_only(text: str) -> float:
    """Penalise third-party imports."""
    allowed = {"os", "re", "json", "time", "typing", "dataclasses", "pathlib"}
    found = re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", text, re.M)
    if not found:
        return 1.0
    roots = {f.split(".")[0] for f in found}
    return len(roots & allowed) / len(roots)

grader = composite([
    Check("parses", parses_python(), 2.0),
    Check("stdlib_only", imports_stdlib_only, 1.0),
])

result = sweep("Write a retry helper", grader=grader, trials=3)
```

A grader that raises is caught and recorded against that variant rather than
killing the sweep — one broken check should not discard the calls you already
paid for.

## Running real code

The strongest grader executes the output. This package does not do that for you,
deliberately: running generated code is a decision about trust and sandboxing
that belongs to the caller, not to a default.

If you do it, do it in a container with no network and no credentials:

```python
def runs_clean(text: str) -> float:
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    if not blocks:
        return 0.0
    # Execute in a sandbox you control. Never in this process.
    return 1.0 if sandbox_exec(blocks[0]).returncode == 0 else 0.0
```

Never `exec()` model output in the same process as your sweep.
