"""promptsweeper: stop guessing at prompts, measure them.

Manual prompt engineering is unmeasured A/B testing with one participant. This
package generates a fixed set of named prompt transformations, runs them, scores
them with a grader you supply, and recommends the *cheapest* one that clears your
quality bar.

Reach for the pieces in this order:

1. :mod:`~promptsweeper.variants` -- generate. Twelve documented strategies,
   each stating what it fixes and when it is a waste of tokens.
2. :mod:`~promptsweeper.score`    -- grade. Deterministic checks, plus detection
   of graders too loose to separate anything.
3. :mod:`~promptsweeper.sweep`    -- run. Cheapest-passing selection, and an
   explicit refusal when the evidence does not support a winner.
4. :mod:`~promptsweeper.store`    -- remember. Winning strategies per task kind,
   optionally written into AIBrain's decision log.

Steps 1 and 2 are pure stdlib and run offline with no API key. Only
:func:`~promptsweeper.sweep.sweep` makes billed calls.
"""

from __future__ import annotations

from .model import DEFAULT_MODEL, HAVE_OPUS5LEAN, Completion, ModelError, count_tokens, price
from .score import Check, Grader, build, composite, is_degenerate, parses_python, valid_json
from .store import Winner, classify, recall, record, stats
from .sweep import SweepResult, Trial, VariantResult, estimate, sweep
from .variants import REDUNDANT_WITH_THINKING, STRATEGIES, Variant, generate

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_MODEL",
    "HAVE_OPUS5LEAN",
    "REDUNDANT_WITH_THINKING",
    "STRATEGIES",
    "Check",
    "Completion",
    "Grader",
    "ModelError",
    "SweepResult",
    "Trial",
    "Variant",
    "VariantResult",
    "Winner",
    "__version__",
    "build",
    "classify",
    "composite",
    "count_tokens",
    "estimate",
    "generate",
    "is_degenerate",
    "parses_python",
    "price",
    "recall",
    "record",
    "stats",
    "sweep",
    "valid_json",
]
