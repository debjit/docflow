"""Tests for the parallel job runner."""

from docflow.core.job_runner import Job, default_concurrency, run_jobs


def test_run_jobs_preserves_order_with_concurrency():
    jobs = [Job(key=f"j{i}", run=lambda i=i: i) for i in range(5)]
    assert run_jobs(jobs, concurrency=2) == [0, 1, 2, 3, 4]


def test_run_jobs_retries_failed_job_once():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("first try")
        return "ok"

    jobs = [
        Job(key="a", run=lambda: "a"),
        Job(key="flaky", run=flaky),
        Job(key="c", run=lambda: "c"),
    ]
    assert run_jobs(jobs, concurrency=2) == ["a", "ok", "c"]
    assert attempts["n"] == 2


def test_run_jobs_progress_running_done_failed():
    messages = []

    def boom():
        raise RuntimeError("nope")

    jobs = [
        Job(key="features/auth", run=lambda: "auth"),
        Job(key="features/billing", run=boom),
    ]
    run_jobs(jobs, concurrency=1, auto_retry=False, on_progress=messages.append)
    text = "\n".join(messages)
    assert any("running" in line and "features/auth" in line for line in messages)
    assert any("done" in line and "features/auth" in line for line in messages)
    assert any("[failed]" in line and "features/billing" in line for line in messages)
    assert "nope" in text


def test_run_jobs_pause_blocks_next_job():
    from threading import Event
    from docflow.core.job_runner import Job, RunControl, run_jobs

    control = RunControl()
    started = Event()
    release = Event()
    order = []

    def first():
        order.append("first")
        control.pause()
        started.set()
        release.wait(timeout=2)
        return "a"

    def second():
        order.append("second")
        return "b"

    jobs = [Job(key="a", run=first), Job(key="b", run=second)]
    from threading import Thread

    result = []

    def worker():
        result.extend(run_jobs(jobs, concurrency=1, auto_retry=False, run_control=control))

    thread = Thread(target=worker)
    thread.start()
    assert started.wait(timeout=2)
    assert order == ["first"]
    release.set()
    thread.join(timeout=0.3)
    assert thread.is_alive()
    control.resume()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result == ["a", "b"]
    assert order == ["first", "second"]


def test_default_concurrency_reads_env(monkeypatch):
    monkeypatch.delenv("DOCFLOW_JOBS", raising=False)
    assert default_concurrency() == 1
    monkeypatch.setenv("DOCFLOW_JOBS", "8")
    assert default_concurrency() == 8
    from docflow.core.job_runner import clamp_concurrency

    assert clamp_concurrency("0") == 1
    assert clamp_concurrency("99") == 16
