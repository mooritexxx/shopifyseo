"""Terminal-state handling for ``run_ai_generation`` (complete / error / cancelled)."""

import pytest

from shopifyseo.dashboard_actions import _ai
from shopifyseo.dashboard_actions._state import (
    AI_JOB_QUEUES,
    AI_JOBS,
    AI_JOBS_LOCK,
    AI_STATE,
    request_ai_cancel,
)
from shopifyseo.exceptions import AICancelledError

SCOPE = "product:demo-handle"


@pytest.fixture
def job(tmp_path):
    """Register a real AI job and hand back (db_path, job_id, event_queue); clean up after."""
    db_path = str(tmp_path / "test.sqlite3")
    job_id, _state = _ai._register_ai_job(SCOPE)
    with AI_JOBS_LOCK:
        events = AI_JOB_QUEUES[job_id]
    try:
        yield db_path, job_id, events
    finally:
        with AI_JOBS_LOCK:
            AI_JOBS.pop(job_id, None)
            AI_JOB_QUEUES.pop(job_id, None)
            AI_STATE["cancel_requested"] = False


def _terminal_events(events):
    drained = []
    while not events.empty():
        drained.append(events.get_nowait())
    return [e for e in drained if e.get("type") in {"done", "error", "cancelled"}]


def test_all_targets_failing_finalizes_as_error(job, monkeypatch):
    db_path, job_id, events = job

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(_ai.dai, "generate_recommendation", _boom)

    result = _ai.run_ai_generation(db_path, SCOPE, job_id)

    state = AI_JOBS[job_id]
    assert state["stage"] == "error"
    assert state["stage_label"] == "AI generation failed"
    assert state["successes"] == 0
    assert state["failures"] == 1
    assert state["last_error"] == "boom"
    assert result["successes"] == 0
    assert result["failures"] == 1
    assert "cancelled" not in result
    assert [e["type"] for e in _terminal_events(events)] == ["error"]


def test_cancel_before_first_target_finalizes_as_cancelled(job, monkeypatch):
    db_path, job_id, events = job

    def _unexpected(*args, **kwargs):
        raise AssertionError("generation should not run for a cancelled job")

    monkeypatch.setattr(_ai.dai, "generate_recommendation", _unexpected)
    request_ai_cancel(job_id)

    result = _ai.run_ai_generation(db_path, SCOPE, job_id)

    state = AI_JOBS[job_id]
    assert state["stage"] == "cancelled"
    assert result["cancelled"] is True
    assert result["successes"] == 0
    assert [e["type"] for e in _terminal_events(events)] == ["cancelled"]


def test_cancel_after_loop_finalizes_as_cancelled(job, monkeypatch):
    """Cancel requested during the last target's work: the post-loop check must be handled."""
    db_path, job_id, events = job

    def _succeed_then_cancel(*args, **kwargs):
        request_ai_cancel(job_id)
        return {"title": "ok"}

    monkeypatch.setattr(_ai.dai, "generate_recommendation", _succeed_then_cancel)

    result = _ai.run_ai_generation(db_path, SCOPE, job_id)

    state = AI_JOBS[job_id]
    assert state["stage"] == "cancelled"
    assert state["stage_label"] == "AI generation cancelled"
    assert result["cancelled"] is True
    assert result["successes"] == 1
    assert [e["type"] for e in _terminal_events(events)] == ["cancelled"]


def test_cancel_raised_inside_generation_still_finalizes_as_cancelled(job, monkeypatch):
    """The pre-existing in-loop handler keeps working (regression guard)."""
    db_path, job_id, events = job

    def _cancelled(*args, **kwargs):
        raise AICancelledError()

    monkeypatch.setattr(_ai.dai, "generate_recommendation", _cancelled)

    result = _ai.run_ai_generation(db_path, SCOPE, job_id)

    assert AI_JOBS[job_id]["stage"] == "cancelled"
    assert result["cancelled"] is True
    assert [e["type"] for e in _terminal_events(events)] == ["cancelled"]


def test_successful_generation_finalizes_as_complete(job, monkeypatch):
    db_path, job_id, events = job

    monkeypatch.setattr(_ai.dai, "generate_recommendation", lambda *a, **k: {"title": "ok"})

    result = _ai.run_ai_generation(db_path, SCOPE, job_id)

    state = AI_JOBS[job_id]
    assert state["stage"] == "complete"
    assert state["stage_label"] == "AI generation complete"
    assert state["successes"] == 1
    assert state["failures"] == 0
    assert state["last_error"] == ""
    assert result["successes"] == 1
    assert "cancelled" not in result
    assert [e["type"] for e in _terminal_events(events)] == ["done"]
