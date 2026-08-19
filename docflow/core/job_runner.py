"""
Reusable parallel job runner with live status, one auto-retry, and readable failures.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, List, Literal, Optional, Sequence


@dataclass
class Job:
    key: str
    run: Callable[[], Any]


@dataclass
class JobState:
    key: str
    status: Literal["pending", "running", "done", "failed"]
    error_message: Optional[str] = None
    result: Any = None


class RunControl:
    """Pause starting new jobs. Running jobs keep going; the UI can freeze log updates separately."""

    def __init__(self) -> None:
        self._gate = threading.Event()
        self._gate.set()
        self._paused = False
        self._lock = threading.Lock()

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._gate.clear()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._gate.set()

    def wait(self) -> None:
        self._gate.wait()


def default_concurrency() -> int:
    """Read DOCFLOW_JOBS as a positive int, otherwise 4."""
    raw = os.getenv("DOCFLOW_JOBS")
    if raw is None or str(raw).strip() == "":
        return 4
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 4


def _failure_message(result: Any, exc: Optional[BaseException]) -> Optional[str]:
    if exc is not None:
        return str(exc)
    if getattr(result, "success", True) is False:
        return getattr(result, "error_message", None) or "job failed"
    return None


def run_jobs(
    jobs: Sequence[Job],
    concurrency: int = 4,
    on_progress: Optional[Callable[[str], None]] = None,
    auto_retry: bool = True,
    run_control: Optional[RunControl] = None,
) -> list:
    """Run jobs with a thread pool, optional one retry of failures, results in input order."""
    job_list = list(jobs)
    if not job_list:
        return []

    workers = max(1, min(int(concurrency) if concurrency else 1, len(job_list)))
    lock = threading.Lock()
    total = len(job_list)
    states: List[JobState] = [JobState(key=job.key, status="pending") for job in job_list]

    def emit(message: str) -> None:
        if on_progress:
            on_progress(message)

    def execute(index: int) -> None:
        if run_control is not None:
            run_control.wait()
        job = job_list[index]
        with lock:
            states[index].status = "running"
            states[index].error_message = None
            running = sum(1 for state in states if state.status == "running")
            emit(f"[{running}/{total} running] {job.key}")
        result: Any = None
        exc: Optional[BaseException] = None
        try:
            result = job.run()
        except Exception as err:  # noqa: BLE001 — caller sees str(exc)
            exc = err
            result = None
        error = _failure_message(result, exc)
        with lock:
            states[index].result = result
            if error:
                states[index].status = "failed"
                states[index].error_message = error
                emit(f"[failed] {job.key}: {error}")
            else:
                states[index].status = "done"
                states[index].error_message = None
                done = sum(1 for state in states if state.status == "done")
                emit(f"[{done}/{total} done] {job.key}")

    def run_wave(indices: Sequence[int]) -> None:
        if not indices:
            return
        wave_workers = max(1, min(workers, len(indices)))
        with ThreadPoolExecutor(max_workers=wave_workers) as pool:
            futures = [pool.submit(execute, index) for index in indices]
            for future in as_completed(futures):
                future.result()

    run_wave(range(total))
    if auto_retry:
        retry_indices = [i for i, state in enumerate(states) if state.status == "failed"]
        run_wave(retry_indices)

    return [state.result for state in states]
