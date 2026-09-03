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
import traceback


def run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs,
                     timeout_seconds=720, fail_messages=None):
    """Runs one `claude -p` call to completion, streaming its stream-json output
    through `on_event`. Returns `(ok, tail_output)`.

    `ok` is True if the process ran to completion -- regardless of what it actually
    produced, since a multi-call orchestration (draft, then a render/QA loop) needs
    to decide per-call what "success" means by checking its own files on disk, not
    this function. `ok` is False on any process-level failure (the CLI missing, an
    unhandled exception while streaming, cancellation): in every False case except
    cancellation, this function has already marked `jobs[job_id]` done+errored
    itself, so the caller should just stop and return without touching the job
    further. `tail_output` is the last ~4000 chars of the subprocess's combined
    stdout/stderr, for the caller's own "produced nothing usable" diagnostics.

    `jobs` is the caller's job registry dict (keyed by job_id) -- kept generic so
    this works for app.py's RUNS (tailoring/revision) and MASTER_CV_JOBS
    (master-CV parsing) alike.
    """
    # The job entry might not exist at all (a caller bug, or -- in principle -- the
    # stale-run sweep already popping it) as well as being flagged cancelled; either way
    # there's nothing to do. Fetched once via .get() and reused below rather than
    # re-indexing `jobs[job_id]` directly, which would raise if the entry is missing.
    job = jobs.get(job_id)
    if job is None or job.get("cancelled"):
        return False, ""

    tail_output = []
    try:
        proc = subprocess.Popen(cmd, cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except FileNotFoundError:
        job.update(done=True, error="The 'claude' CLI was not found on PATH.")
        return False, ""
    job["proc"] = proc

    # `for line in proc.stdout` blocks whenever the subprocess goes quiet (e.g. a hung
    # nested API call), so a timeout check inside the loop body never fires during that
    # silence. A watchdog timer kills the process on a wall-clock deadline regardless.
    watchdog = threading.Timer(timeout_seconds, proc.kill)
    watchdog.start()
    try:
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
            return False, ""

        return True, "".join(tail_output)[-4000:]
    except Exception:
        # Anything unexpected here (a broken on_event, a stdout read error, ...) would
        # otherwise kill this background thread silently, leaving the job stuck at
        # done=False forever with no way for the polling client to know. Always
        # finalize the job instead.
        watchdog.cancel()
        proc.kill()
        # .get() rather than direct indexing -- the entry may be gone entirely (e.g. the
        # stale-run sweep already popped it), not just flagged cancelled, and either way
        # there's nothing left to finalize.
        job = jobs.get(job_id)
        if job is None or job.get("cancelled"):
            return False, ""
        detail = "".join(tail_output)[-4000:]
        print(
            f"[cv-tailor] job {job_id} crashed:\n{traceback.format_exc()}\nRecent output:\n{detail}",
            file=sys.stderr,
        )
        messages = fail_messages or ["Something went wrong on our end. Please try again."]
        job.update(done=True, error=random.choice(messages))
        return False, ""


def run_process_and_finalize(job_id, cmd, project_root, run_dir, on_event, success_check, jobs,
                              timeout_seconds=720, fail_messages=None, on_success=None):
    """Runs `cmd` as a single `claude -p` call via `run_single_call`, then marks
    `jobs[job_id]` done/errored based on `success_check`. For call sites that only
    ever need one call (currently just master-CV parsing) -- a multi-call flow
    (fresh tailoring, revision) orchestrates `run_single_call` directly instead,
    since it needs to inspect/act between calls rather than finalize after just one.

    `success_check` decides whether the attempt produced a usable result.
    `on_success`, if given, runs after success_check passes but BEFORE
    `done=True` is set -- so it can mutate jobs[job_id] first (e.g. move
    output into permanent storage and repoint paths) and have that be
    visible the moment a client observes done=True. If `on_success` sets
    jobs[job_id]["error"] itself (e.g. the move failed), that error is
    preserved instead of being overwritten by the normal success update.
    """
    ok, tail_output = run_single_call(job_id, cmd, project_root, run_dir, on_event, jobs, timeout_seconds, fail_messages)
    if not ok:
        return

    # success_check/on_success run real logic of their own (file moves, JSON parsing, DB
    # writes for master-CV parsing) that can raise -- wrapped for the same reason the
    # streaming loop in run_single_call is: an unhandled exception here would otherwise
    # leave the job stuck at done=False forever.
    try:
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
            print(f"[cv-tailor] job {job_id} failed without an output file:\n{tail_output}", file=sys.stderr)
            messages = fail_messages or ["Something went wrong on our end. Please try again."]
            jobs[job_id].update(done=True, error=random.choice(messages))
    except Exception:
        job = jobs.get(job_id)
        if job is None or job.get("cancelled"):
            return
        print(
            f"[cv-tailor] job {job_id} crashed after its process finished:\n{traceback.format_exc()}",
            file=sys.stderr,
        )
        messages = fail_messages or ["Something went wrong on our end. Please try again."]
        job.update(done=True, error=random.choice(messages))
