# The twelve strategies

Each entry states what the transformation changes, the failure mode it targets,
and when it is a waste of tokens. That last column is the one that saves money:
a strategy applied where its failure mode never occurs is pure cost.

Listed in registry order, which is also the order the sweeper reports. Ties on
score resolve to whichever appears first, so the cheap transformations lead.

---

## `baseline`

**Changes:** nothing. Sends the task verbatim.

**Targets:** nothing — it is the control.

**Wasteful when:** never. Without it you have no reference point, and every
"improvement" is unfalsifiable.

---

## `minimal`

**Changes:** strips politeness and hedging — *please*, *could you*, *I'd like*,
*if possible*, *thanks*.

**Targets:** paying input tokens for words that carry no instruction.

**Wasteful when:** the task was already terse. Savings round to zero and you
have spent a sweep slot to learn that.

**Note:** the highest-yield strategy on prompts written conversationally, and
worthless on prompts written as specifications. Check the token delta in
`generate` before bothering to run it.

---

## `direct`

**Changes:** appends an instruction to answer with no preamble, no restatement,
no closing summary.

**Targets:** output tokens spent restating the question. At $25/MTok output on
Opus 5, a 60-word preamble on every request is a real line item.

**Wasteful when:** the model is already terse, or you *want* the reasoning
visible.

---

## `output_contract`

**Changes:** specifies the exact output shape — deliverable in one fenced block,
nothing before it, at most three assumption bullets after.

**Targets:** output that something downstream has to parse and cannot.

**Wasteful when:** a human reads the output directly. Format enforcement costs
input tokens to solve a problem you do not have.

---

## `negative`

**Changes:** lists prohibitions — no placeholders, no skipped error handling, no
narration, no gratuitous dependencies.

**Targets:** known recurring failure modes, stated as rules.

**Wasteful when:** the listed failures were not happening. Prohibitions are only
worth their tokens against failures you have actually observed; derive the list
from your own corrections rather than copying it wholesale.

---

## `spec`

**Changes:** restates the task as numbered requirements plus a checkable
acceptance-criteria list.

**Targets:** silently dropping half of a multi-part request — the dominant
failure on compound tasks.

**Wasteful when:** single-requirement tasks. The scaffolding outweighs the task.

---

## `checklist`

**Changes:** converts the task into ordered steps to complete in sequence.

**Targets:** out-of-order work that breaks dependencies between steps.

**Wasteful when:** the task has no ordering constraint. Imposing a sequence on
independent work adds tokens and can *reduce* quality by forcing a false order.

---

## `decompose`

**Changes:** asks for sub-problems to be listed first, then solved in order,
capped at five.

**Targets:** large tasks answered shallowly because they were treated as one
step.

**Wasteful when:** the task is atomic. You pay for a list containing one item.

---

## `role`

**Changes:** assigns a senior-engineer persona and a production-code standard.

**Targets:** toy-quality output — no error handling, no edge cases, no types.

**Wasteful when:** on frontier models. Persona prompting is largely cosmetic
where capability is already present, and it costs real input tokens. Treat a win
here with suspicion and re-run at higher `--trials` before believing it.

---

## `self_check` \*

**Changes:** requires a re-read against the task and a one-line statement of
what was verified.

**Targets:** confidently wrong first drafts.

**Wasteful when:** thinking is enabled. The model already self-checks
internally, so this duplicates work you are billed for twice.

---

## `cot` \*

**Changes:** asks for step-by-step reasoning before the answer, capped at 100
words.

**Targets:** arithmetic and multi-constraint logic answered by pattern-matching.

**Wasteful when:** on Claude Opus 5, where thinking is on by default. An explicit
chain-of-thought instruction usually buys nothing and bills as output. This
strategy earns its place on models without default reasoning.

---

## `few_shot`

**Changes:** prepends a worked example to anchor shape and detail level.

**Targets:** output that is correct but formatted or scoped unlike what you
wanted.

**Wasteful when:** the example is long relative to the task. The example is paid
for on *every* request — the most expensive strategy here by token count, and the
one most worth caching if you adopt it.

---

\* Marked redundant with Opus 5's default thinking. `--skip-redundant` drops
both and says so in the report.

## Choosing a subset

Sweeping all twelve at three trials is 36 calls. Narrow by failure mode instead:

| Symptom | Try |
|---|---|
| Output too long or padded | `minimal,direct,output_contract` |
| Parts of the request ignored | `spec,checklist` |
| Placeholders, missing error handling | `negative,role` |
| Unparseable for a downstream consumer | `output_contract,few_shot` |
| Shallow answers to big tasks | `decompose,spec` |
| Wrong format, right content | `few_shot,output_contract` |

```bash
prompt-sweep run "<task>" --only minimal,direct,output_contract --python-code --trials 3
```
