import claude_runner


def test_run_single_call_marks_job_errored_if_cli_missing(tmp_path, monkeypatch):
    def fake_popen(*a, **kw):
        raise FileNotFoundError()

    monkeypatch.setattr(claude_runner.subprocess, "Popen", fake_popen)
    jobs = {"j1": {"cancelled": False}}

    ok, tail = claude_runner.run_single_call("j1", ["claude"], tmp_path, tmp_path, lambda b: None, jobs)

    assert ok is False
    assert jobs["j1"]["done"] is True
    assert "not found" in jobs["j1"]["error"]


def test_run_single_call_marks_job_errored_on_unexpected_exception(tmp_path, monkeypatch):
    # Simulates a crash inside on_event -- the exact class of bug A1 was meant to catch:
    # previously this would silently kill the background thread, leaving done=False forever.
    class FakeProc:
        stdout = ['{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}\n']

        def kill(self):
            pass

        def wait(self, timeout=None):
            pass

    monkeypatch.setattr(claude_runner.subprocess, "Popen", lambda *a, **kw: FakeProc())

    def broken_on_event(block):
        raise RuntimeError("boom")

    jobs = {"j1": {"cancelled": False}}
    ok, tail = claude_runner.run_single_call("j1", ["claude"], tmp_path, tmp_path, broken_on_event, jobs)

    assert ok is False
    assert jobs["j1"]["done"] is True
    assert jobs["j1"]["error"]


def test_run_single_call_missing_job_entry_does_not_raise(tmp_path, monkeypatch):
    # The job entry can be gone entirely by the time the exception handler runs (e.g. the
    # stale-run sweep already popped it) -- must not raise trying to update a missing key.
    class FakeProc:
        stdout = ['{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}\n']

        def kill(self):
            pass

        def wait(self, timeout=None):
            pass

    monkeypatch.setattr(claude_runner.subprocess, "Popen", lambda *a, **kw: FakeProc())

    def broken_on_event(block):
        raise RuntimeError("boom")

    jobs = {}  # job entry already removed
    ok, tail = claude_runner.run_single_call("gone", ["claude"], tmp_path, tmp_path, broken_on_event, jobs)

    assert ok is False


def test_run_process_and_finalize_marks_errored_when_on_success_raises(tmp_path, monkeypatch):
    # Regression test: success_check/on_success used to run outside any exception
    # handling in the multi-call refactor -- a crash there (a bad file move, malformed
    # JSON, ...) would leave the job stuck at done=False forever, exactly what A1 fixed
    # for the streaming part alone.
    class FakeProc:
        stdout = []

        def kill(self):
            pass

        def wait(self, timeout=None):
            pass

    monkeypatch.setattr(claude_runner.subprocess, "Popen", lambda *a, **kw: FakeProc())

    jobs = {"j1": {"cancelled": False}}

    def broken_on_success():
        raise RuntimeError("disk full")

    claude_runner.run_process_and_finalize(
        "j1", ["claude"], tmp_path, tmp_path, lambda b: None,
        success_check=lambda: True, jobs=jobs, on_success=broken_on_success,
    )

    assert jobs["j1"]["done"] is True
    assert jobs["j1"]["error"]


def test_run_process_and_finalize_happy_path_still_works(tmp_path, monkeypatch):
    class FakeProc:
        stdout = []

        def kill(self):
            pass

        def wait(self, timeout=None):
            pass

    monkeypatch.setattr(claude_runner.subprocess, "Popen", lambda *a, **kw: FakeProc())
    jobs = {"j1": {"cancelled": False, "percent": 0}}

    claude_runner.run_process_and_finalize(
        "j1", ["claude"], tmp_path, tmp_path, lambda b: None,
        success_check=lambda: True, jobs=jobs,
    )

    assert jobs["j1"]["done"] is True
    assert jobs["j1"]["error"] is None
    assert jobs["j1"]["percent"] == 100
