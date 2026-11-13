import json
import subprocess

import pytest
import run_experiment as runner


def completed(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


def test_sender_failure_is_returned_and_receiver_early_exit_is_detected(monkeypatch):
    monkeypatch.setattr(runner, "_docker", lambda *a, **k: completed("false 23"))
    assert runner._wait_for_container("sender", 1) == 23

    def inspect(*args, **kwargs):
        return completed("true 0" if args[-1] == "sender" else "false")

    monkeypatch.setattr(runner, "_docker", inspect)
    with pytest.raises(RuntimeError, match="stopped before sender"):
        runner._wait_for_container("sender", 1, required_running=("receiver",))


@pytest.mark.parametrize(
    "failure", ["exit", "timeout", "receiver_exit", "stale_image", "bad_content", "missing_work"]
)
def test_failed_sender_still_saves_logs_and_cleans_its_project(monkeypatch, tmp_path, failure):
    spec = json.loads((runner.ROOT / "experiments.json").read_text())
    calls = []

    def compose(env, *args, **kwargs):
        calls.append((env["COMPOSE_PROJECT_NAME"], args))
        return completed(args[-1] + "-id" if "ps" in args else "")

    def docker(*args, **kwargs):
        if args[0] == "inspect":
            return completed(
                json.dumps(
                    [{"Config": {"Env": []}, "Mounts": [], "Image": "test-image"} for _ in range(3)]
                )
            )
        if args[:2] == ("image", "inspect"):
            return completed('[{"Created":"test"}]')
        return completed("test-version")

    monkeypatch.setattr(runner, "_compose", compose)
    monkeypatch.setattr(runner, "_docker", docker)
    monkeypatch.setattr(
        runner,
        "_image_runtime_info",
        lambda *_: {
            "python_version": "test-version",
            "source_sha256": {"src/stale.py": "old"}
            if failure == "stale_image"
            else {
                name: digest
                for name, digest in runner._source_hashes().items()
                if name.startswith("src/")
            },
        },
    )
    exits = iter([23 if failure == "exit" else 0, 23 if failure == "receiver_exit" else 0])

    def wait(*args, **kwargs):
        if failure == "timeout":
            raise TimeoutError("sender deadline exceeded")
        return next(exits)

    monkeypatch.setattr(runner, "_wait_for_container", wait)
    monkeypatch.setattr(runner, "_logs", lambda container: "diagnostic for " + container)
    if failure in ("bad_content", "missing_work"):
        monkeypatch.setattr(runner, "_parse_sender_record", lambda *_: {"symbols": [None, 0, None]})
        monkeypatch.setattr(
            runner,
            "analyze_run",
            lambda *a, **k: {
                "requests": {
                    "body_hashes_complete": failure != "bad_content",
                    "unique_body_sha256": 1,
                    "request_bytes": [64],
                    "work_requests": 2,
                },
                "receiver_decoded_independently": True,
            },
        )
    monkeypatch.setattr(
        runner, "_copy_from_container", lambda container, source, target: target.write_text("trace")
    )
    run_dir = tmp_path / "failed-run"
    error = TimeoutError if failure == "timeout" else RuntimeError
    message = {
        "exit": "sender exited with 23",
        "timeout": "deadline exceeded",
        "receiver_exit": "receiver exited with 23",
        "stale_image": "image source",
        "bad_content": "constant fixed-size",
        "missing_work": "request count",
    }[failure]
    with pytest.raises(error, match=message):
        runner._run_once(spec["scenarios"][0], spec["defaults"], 0, run_dir)
    assert (run_dir / "sender.log").read_text() == "diagnostic for sender-id"
    assert (run_dir / "receiver.log").exists()
    assert (run_dir / "receiver.json").read_text() == "trace"
    assert not (run_dir / "analysis.json").exists()
    assert calls[-1][1] == ("--profile", "noise", "down", "-v", "--remove-orphans")
    assert len({project for project, _ in calls}) == 1
    stop_index = next(i for i, (_, args) in enumerate(calls) if "stop" in args)
    assert stop_index < len(calls) - 1


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_startup_failure_preserves_diagnostics_and_primary_error(
    monkeypatch, tmp_path, cleanup_fails
):
    spec = json.loads((runner.ROOT / "experiments.json").read_text())
    calls = []

    def compose(env, *args, **kwargs):
        calls.append(args)
        if args[0] == "up":
            return completed("startup failed", 1)
        if "ps" in args:
            return completed(args[-1] + "-id" if args[-1] in ("receiver", "resource") else "")
        if "down" in args and cleanup_fails:
            return completed("cleanup failed", 1)
        return completed()

    monkeypatch.setattr(runner, "_compose", compose)
    monkeypatch.setattr(runner, "_logs", lambda *_: "startup diagnostic")
    monkeypatch.setattr(
        runner, "_copy_from_container", lambda a, b, target: target.write_text("trace")
    )
    run_dir = tmp_path / "startup-failed"
    with pytest.raises(RuntimeError, match="compose up failed") as error:
        runner._run_once(spec["scenarios"][0], spec["defaults"], 0, run_dir)
    assert (run_dir / "receiver.log").read_text() == "startup diagnostic"
    assert (run_dir / "resource.log").exists()
    assert (run_dir / "receiver.json").read_text() == "trace"
    assert not (run_dir / "analysis.json").exists()
    assert "down" in calls[-1]
    if cleanup_fails:
        assert "Cleanup also failed" in error.value.__notes__[0]


def test_minimal_spec_uses_actual_raw_defaults():
    env, bits = runner._channel_env({"name": "minimal", "bits": 8}, 0, {})
    assert env["CHANNEL_MODE"] == "raw"
    assert len(bits) == 8 and bits == env["CHANNEL_BITS"]
    assert runner._sender_timeout(env) > 0


def test_inherited_profiles_cannot_turn_a_clean_scenario_noisy(monkeypatch):
    monkeypatch.setenv("COMPOSE_PROFILES", "noise")
    env, _ = runner._channel_env({"name": "clean", "bits": 8}, 0, {})
    assert env["COMPOSE_PROFILES"] == ""


@pytest.mark.parametrize("repeats", [0, -1, 1.5, True])
def test_bad_repeat_counts_are_rejected_before_docker(monkeypatch, tmp_path, repeats):
    spec = {"scenarios": [{"name": "bad", "repeats": repeats}]}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--config", str(path)])

    def unexpected(*args, **kwargs):
        pytest.fail("Docker should not be touched for an invalid specification")

    monkeypatch.setattr(runner, "_docker", unexpected)
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
