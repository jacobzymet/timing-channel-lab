import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from run_experiment import _channel_env, _sender_timeout, _verify_isolation

from timingchannel.receiver import main as receive
from timingchannel.sender import _raw_bits

ROOT = Path(__file__).resolve().parents[1]


def containers():
    return [
        {"Config": {"Env": ["CHANNEL_PORT=8080"]}, "Mounts": [{"Source": "receiver-only"}]},
        {"Config": {"Env": ["CHANNEL_BITS=101"]}, "Mounts": []},
    ]


@pytest.mark.parametrize("name", ["CHANNEL_MESSAGE", "CHANNEL_BITS"])
def test_payload_in_receiver_environment_fails_closed(monkeypatch, name):
    monkeypatch.setenv(name, "secret")
    monkeypatch.setattr(sys, "argv", ["receiver"])
    with pytest.raises(ValueError, match="payload variables"):
        receive()
    inspected = containers()
    inspected[0]["Config"]["Env"].append(name + "=secret")
    with pytest.raises(RuntimeError, match="leaked"):
        _verify_isolation(inspected)


def test_mount_isolation_is_verified():
    inspected = containers()
    assert all(_verify_isolation(inspected).values())
    inspected[1]["Mounts"] = copy.deepcopy(inspected[0]["Mounts"])
    with pytest.raises(RuntimeError, match="filesystem"):
        _verify_isolation(inspected)


def test_mount_ancestors_and_private_host_bind_mounts_are_rejected():
    inspected = containers()
    inspected[0]["Mounts"] = [{"Source": "/host/output"}]
    inspected[1]["Mounts"] = [{"Source": "/host"}]
    with pytest.raises(RuntimeError, match="filesystem"):
        _verify_isolation(inspected)
    inspected[1]["Mounts"] = []
    inspected[0]["Mounts"][0]["Type"] = "bind"
    with pytest.raises(RuntimeError, match="bind mounts"):
        _verify_isolation(inspected)


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI not installed")
def test_rendered_compose_keeps_payload_only_on_sender(monkeypatch):
    monkeypatch.setenv("CHANNEL_MESSAGE", "PRIVATE_PAYLOAD")
    monkeypatch.setenv("CHANNEL_BITS", "101100")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "docker-compose.yml"),
            "--profile",
            "noise",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    for role in ("receiver", "noise", "resource"):
        assert not {"CHANNEL_MESSAGE", "CHANNEL_BITS"} & services[role]["environment"].keys()
    assert services["sender"]["environment"]["CHANNEL_MESSAGE"] == "PRIVATE_PAYLOAD"
    assert services["sender"]["depends_on"]["receiver"]["condition"] == "service_healthy"
    assert services["receiver"]["environment"]["CHANNEL_DURATION_S"] == "0"
    assert services["receiver"]["environment"]["CHANNEL_EVENT_THRESHOLD_MS"] == "50"


def test_sender_deadline_scales_with_transmission_and_projects_are_unique():
    spec = json.loads((ROOT / "experiments.json").read_text())
    scenario = spec["scenarios"][0]
    env, _ = _channel_env(scenario, 0, spec["defaults"])
    small = _sender_timeout(env)
    env["CHANNEL_MESSAGE"] = "X" * 100
    assert _sender_timeout(env) > small * 5
    other, _ = _channel_env(scenario, 0, spec["defaults"])
    assert other["COMPOSE_PROJECT_NAME"] != env["COMPOSE_PROJECT_NAME"]


def test_raw_bits_rejects_silent_corruption():
    assert _raw_bits("10 01\n") == [1, 0, 0, 1]
    with pytest.raises(ValueError):
        _raw_bits("10garbage01")
