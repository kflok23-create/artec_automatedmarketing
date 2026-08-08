"""A healthy scheduler and a wedged one looked identical, and that cost hours.

    2026-08-08T13:41:02Z  Starting Container
    (nothing, for as long as the deployment lived)

I read that silence as a hang, three deployments running. It led to a real defect — an
unbounded DB connect that `wait_for_schema`'s retry loop could never catch — which was worth
fixing on its own merits and is fixed. **It was not the cause of the silence.** The silence
survived the fix, which is how I learned the difference.

WHY: `artec-scheduler` reports itself with `print()` to stdout, and stdout is BLOCK-BUFFERED
when it is a pipe rather than a terminal — roughly 8KB has to accumulate before anything is
written. The boot banner is about 600 bytes. So a scheduler that booted perfectly and a
scheduler stuck on its first statement produce byte-identical output: one line from Railway's
own container runtime, and nothing from the process.

`artec api` was legible throughout because it logs via `logging` to STDERR, which is not
block-buffered. Same platform, same image, opposite observability — the difference was never
the health of the service, only which stream it wrote to.

THE DIAGNOSTIC LESSON, which is the durable part: an absent log line is only evidence of a
hang if a healthy run is GUARANTEED to have flushed one by then. That guarantee did not
exist here, so the observation carried no information and I treated it as though it carried
a lot. It is the standing review question in its logging costume — one side of the
comparison (what a healthy run looks like) was never established.
"""

from __future__ import annotations

import json


def _scheduler_config(repo_root) -> dict:
    with open(repo_root / "railway.scheduler.json", encoding="utf-8") as fh:
        return json.load(fh)


def test_the_scheduler_starts_unbuffered(repo_root):
    """THE FIX. Without `-u`, the boot banner sits in a buffer and the service is
    indistinguishable from a wedged one."""
    command = _scheduler_config(repo_root)["deploy"]["startCommand"]
    assert command.startswith("python -u "), (
        f"scheduler startCommand is {command!r}. Without -u, stdout is block-buffered and "
        "~600 bytes of boot banner never reach Railway — a healthy scheduler looks exactly "
        "like a hung one, which is how 2026-08-08 was misdiagnosed.")


def test_it_still_runs_the_scheduler_module(repo_root):
    """The obvious way to break this while fixing it."""
    assert _scheduler_config(repo_root)["deploy"]["startCommand"].endswith("-m app.scheduler")


def test_the_scheduler_reports_itself_with_print_which_is_why_u_matters(repo_root):
    """Ties the flag to the reason. If this service is ever converted to `logging` (stderr,
    line-buffered), `-u` stops being load-bearing — but until then removing it re-creates
    the blind spot, and this test says so rather than leaving the flag looking decorative.
    """
    source = (repo_root / "app" / "scheduler.py").read_text(encoding="utf-8")
    assert "print(" in source, (
        "app/scheduler.py no longer uses print(). If it moved to `logging` on stderr, the "
        "-u flag and this test can go — but confirm the new stream is unbuffered first.")


def test_the_api_is_unbuffered_too(repo_root):
    """THIS TEST USED TO ASSERT THE OPPOSITE, AND WAS WRONG.

    It was `test_the_api_needs_no_such_flag`, and it reasoned that the api "logs to stderr
    via uvicorn/`logging`, which is not block-buffered". True of uvicorn's own lines — and
    irrelevant to the ones that matter. EVERY `/commands/*` mirror reports progress through
    `RunRecorder.log()`, which is `print()` to STDOUT.

    So an operator-triggered `POST /commands/render` produced no visible output at all while
    it ran, and an in-flight request was indistinguishable from one that never arrived. I
    fixed the identical defect on the scheduler the day before, wrote this test to record
    why the api was different, and the reason I gave was false — a control case that
    cemented the bug instead of catching it.

    A test that explains why the other thing is fine deserves the same scrutiny as the fix.
    """
    with open(repo_root / "railway.json", encoding="utf-8") as fh:
        command = json.load(fh)["deploy"]["startCommand"]
    assert command.startswith("python -u "), (
        f"api startCommand is {command!r}. Without -u, RunRecorder's print() output is "
        "block-buffered and every operator-triggered command runs blind.")
    assert "uvicorn app.main:app" in command


def test_every_railway_service_that_prints_starts_unbuffered(repo_root):
    """The generalisation, so the third instance does not need its own outage. Any service
    whose start command runs python must be unbuffered, because `RunRecorder.log()` is
    shared by the scheduler and every command mirror alike."""
    for config in ("railway.json", "railway.scheduler.json"):
        with open(repo_root / config, encoding="utf-8") as fh:
            command = json.load(fh)["deploy"]["startCommand"]
        assert command.startswith("python -u "), f"{config}: {command!r} is buffered"
