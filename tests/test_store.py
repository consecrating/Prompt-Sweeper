"""Store tests. The ledger path is redirected to a tmp dir, so nothing leaks."""

from __future__ import annotations

import json

import pytest

from promptsweeper.store import (
    STORE_ENV,
    Winner,
    classify,
    load,
    recall,
    record,
    record_to_aibrain,
    stats,
    store_path,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv(STORE_ENV, str(tmp_path / "winners.json"))
    # Keep AIBrain out of unit tests entirely.
    monkeypatch.setenv("AIBRAIN_ROOT", str(tmp_path / "no-aibrain"))
    return tmp_path


def make_winner(**kw) -> Winner:
    defaults = dict(
        task_kind="code-generation",
        strategy="direct",
        model="claude-opus-5",
        score=1.0,
        cost_usd=0.01,
        savings_vs_baseline=0.4,
        trials=3,
        graded=True,
        task_excerpt="write a function",
    )
    defaults.update(kw)
    return Winner(**defaults)


def test_store_path_honours_env(isolated_store):
    assert store_path() == isolated_store / "winners.json"


def test_load_is_empty_when_absent():
    assert load() == []


def test_load_survives_a_corrupt_ledger(isolated_store):
    (isolated_store / "winners.json").write_text("{not json", encoding="utf-8")
    assert load() == []


def test_load_rejects_non_list_content(isolated_store):
    (isolated_store / "winners.json").write_text('{"a": 1}', encoding="utf-8")
    assert load() == []


def test_record_appends_and_creates_parents():
    info = record(make_winner(), to_aibrain=False)
    assert info["entries"] == 1
    record(make_winner(strategy="spec"), to_aibrain=False)
    assert len(load()) == 2


def test_record_reports_missing_aibrain_rather_than_failing():
    info = record(make_winner(), to_aibrain=True)
    assert "not installed" in info["aibrain"]


def test_classify_buckets_tasks():
    assert classify("Write a Python function to parse dates") == "code-generation"
    assert classify("Debug this traceback, it is failing") == "debugging"
    assert classify("Refactor and simplify this module") == "refactor"
    assert classify("Write pytest unit tests with coverage") == "test"
    assert classify("Extract the JSON fields from this page") == "data-extraction"


def test_classify_falls_back_to_general():
    assert classify("zzzz qqqq") == "general"


def test_recall_returns_none_without_history():
    assert recall("Write a Python function") is None


def test_recall_ignores_ungraded_entries():
    record(make_winner(graded=False), to_aibrain=False)
    assert recall("Write a Python function to parse dates") is None


def test_recall_prefers_score_then_cost():
    record(make_winner(strategy="spec", score=0.8, cost_usd=0.001), to_aibrain=False)
    record(make_winner(strategy="direct", score=1.0, cost_usd=0.02), to_aibrain=False)
    record(make_winner(strategy="minimal", score=1.0, cost_usd=0.005), to_aibrain=False)

    found = recall("Write a Python function to parse dates")
    assert found is not None
    assert found["strategy"] == "minimal"  # ties on score, wins on cost
    assert found["observations"] == 3


def test_recall_is_scoped_to_task_kind():
    record(make_winner(task_kind="debugging", strategy="checklist"), to_aibrain=False)
    assert recall("Write a Python function to parse dates") is None
    assert recall("Debug this failing traceback")["strategy"] == "checklist"


def test_stats_summarises_by_kind():
    record(make_winner(strategy="direct"), to_aibrain=False)
    record(make_winner(strategy="direct"), to_aibrain=False)
    record(make_winner(task_kind="debugging", strategy="spec"), to_aibrain=False)

    data = stats()
    assert data["total"] == 3
    assert data["graded"] == 3
    assert data["by_task_kind"]["code-generation"]["direct"] == 2
    assert data["by_task_kind"]["debugging"]["spec"] == 1


def test_saved_ledger_is_valid_json(isolated_store):
    record(make_winner(), to_aibrain=False)
    raw = (isolated_store / "winners.json").read_text(encoding="utf-8")
    assert isinstance(json.loads(raw), list)



# ---------------------------------------------------------------------------
# AIBrain write guard
#
# A decision in a persistent memory layer is read later as evidence, and
# nothing in it reveals that its numbers were never measured. These tests pin
# the refusals that stop an unmeasured result becoming a durable claim.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_aibrain(tmp_path, monkeypatch):
    """A minimal AIBrain checkout that records what brain.sh was called with."""
    root = tmp_path / "AIBrain"
    (root / "scripts").mkdir(parents=True)
    log = root / "calls.log"
    script = root / "scripts" / "brain.sh"
    script.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> "$(dirname "$0")/../calls.log"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("AIBRAIN_ROOT", str(root))
    return root, log


def test_ungraded_winner_is_refused_and_never_written(fake_aibrain):
    root, log = fake_aibrain
    status = record_to_aibrain(make_winner(graded=False))
    assert status is not None
    assert "refused" in status
    assert "ungraded" in status
    assert not log.exists(), "an ungraded result must never reach the decision log"


def test_zero_trial_winner_is_refused(fake_aibrain):
    root, log = fake_aibrain
    status = record_to_aibrain(make_winner(trials=0))
    assert "refused" in status
    assert not log.exists()


def test_graded_winner_is_written_with_provenance(fake_aibrain):
    root, log = fake_aibrain
    status = record_to_aibrain(make_winner(graded=True, trials=3))
    assert "recorded in AIBrain" in status
    args = log.read_text(encoding="utf-8")
    assert "decide" in args
    # Provenance marker makes the entry attributable and auditable later.
    assert "prompt-sweeper:" in args
    assert "3 graded trial(s)" in args


def test_negative_savings_is_not_logged_as_cheaper(fake_aibrain):
    """A quality win that costs more must not be recorded as a saving."""
    root, log = fake_aibrain
    record_to_aibrain(make_winner(savings_vs_baseline=-7.14))
    args = log.read_text(encoding="utf-8")
    assert "more expensive than baseline" in args
    assert "cheaper" not in args


def test_positive_savings_reads_as_cheaper(fake_aibrain):
    root, log = fake_aibrain
    record_to_aibrain(make_winner(savings_vs_baseline=0.4))
    args = log.read_text(encoding="utf-8")
    assert "40% cheaper than baseline" in args


def test_record_surfaces_the_refusal_to_the_caller(fake_aibrain):
    info = record(make_winner(graded=False), to_aibrain=True)
    # The local ledger still gets the row; only the durable claim is withheld.
    assert info["entries"] == 1
    assert "refused" in info["aibrain"]


def test_brain_script_failure_is_reported_not_raised(tmp_path, monkeypatch):
    root = tmp_path / "AIBrain"
    (root / "scripts").mkdir(parents=True)
    script = root / "scripts" / "brain.sh"
    script.write_text("#!/usr/bin/env bash\necho 'boom' >&2\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("AIBRAIN_ROOT", str(root))

    status = record_to_aibrain(make_winner())
    assert "failed" in status
    # Losing a bookkeeping write must not discard calls already paid for.
