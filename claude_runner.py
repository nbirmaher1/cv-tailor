"""Shared subprocess/streaming/cancellation plumbing for shelling out to a
headless `claude -p` session -- used by fresh tailoring, revisions, and
master-CV parsing alike, so this logic lives in one place instead of being
copy-pasted per call site.
"""
import json
import random
import subprocess
import sys
import threading


def run_process_and_finalize(job_id, cmd, project_root, run_dir, on_event, success_check, jobs,
                              timeout_seconds=720, fail_messages=None, on_success=None):
    """Runs `cmd` as a subprocess, streams its stream-json output through
    `on_event` for progress updates, then marks `jobs[job_id]` done/errored.

    `jobs` is the caller's job registry dict (keyed by job_id) -- kept generic
    so this works for app.py's RUNS (tailoring/revision) and a future
    MASTER_CV_JOBS (master-CV parsing) alike.

    `success_check` decides whether the attempt produced a usable result.
    `on_success`, if given, runs after success_check passes but BEFORE
    `done=True` is set -- so it can mutate jobs[job_id] first (e.g. move
    output into permanent storage and repoint paths) and have that be
    visible the moment a client observes done=True. If `on_success` sets
    jobs[job_id]["error"] itself (e.g. the move failed), that error is
    preserved instead of being overwritten by the normal success update.
    """
    # The cancel endpoint may run (on the request thread) before this background thread
    # even gets here -- don't start the subprocess for an attempt that's already cancelled.
    if jobs.get(job_id, {}).get("cancelled"):
        return

    tail_output = []
    try:
        proc = subprocess.Popen(cmd, cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError:
        jobs[job_id].update(done=True, error="The 'claude' CLI was not found on PATH.")
        return
    jobs[job_id]["proc"] = proc

    # `for line in proc.stdout` blocks whenever the subprocess goes quiet (e.g. a hung
    # nested API call), so a timeout check inside the loop body never fires during that
    # silence. A watchdog timer kills the process on a wall-clock deadline regardless.
    watchdog = threading.Timer(timeout_seconds, proc.kill)
    watchdog.start()
    for line in proc.stdout:
        tail_output.append(line)
        tail_output[:] = tail_output[-200:]

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            on_event(block)

    watchdog.cancel()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()

    if jobs.get(job_id, {}).get("cancelled"):
        # The cancel endpoint already finalized this attempt's state (and, for a fresh
        # run, deleted its directory); nothing left to do here.
        return

    error_file = run_dir / "error.txt"
    if success_check():
        if on_success:
            on_success()
        if jobs[job_id].get("error"):
            jobs[job_id]["done"] = True
        else:
            jobs[job_id].update(step="Done!", percent=100, done=True, error=None)
    elif error_file.exists():
        # A deliberate, actionable message the skill wrote itself (e.g. "the job URL is
        # blocked, please paste the text instead") -- show it as-is, not as a joke.
        jobs[job_id].update(done=True, error=error_file.read_text())
    else:
        # An unexplained internal failure (crash, timeout, non-zero exit). Not actionable
        # for the user, so log the real detail server-side and show a friendly message instead.
        detail = "".join(tail_output)[-4000:]
        print(f"[cv-tailor] job {job_id} failed without an output file:\n{detail}", file=sys.stderr)
        messages = fail_messages or ["Something went wrong on our end. Please try again."]
        jobs[job_id].update(done=True, error=random.choice(messages))
