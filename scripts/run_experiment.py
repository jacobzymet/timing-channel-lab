"""Run every experiment against docker compose and write a results table.

This is host-side orchestration only.  The containers themselves are stdlib
Python and start with no extra packages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
RESULTS = ROOT / "results" / "calibrated"
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from timingchannel.analysis import _format_summary, analyze_run  # noqa: E402
from timingchannel.codec import frame_symbols, raw_symbols  # noqa: E402
from timingchannel.config import ChannelConfig  # noqa: E402
from timingchannel.wire import write_json_atomic  # noqa: E402


def _compose(
    env: dict[str, str], *args: str, timeout: float | None = 180
) -> subprocess.CompletedProcess[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        env.get("COMPOSE_PROJECT_NAME", "timing-channel-lab"),
        "-f",
        str(COMPOSE),
        "--project-directory",
        str(ROOT),
        *args,
    ]
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _require_ok(proc: subprocess.CompletedProcess[str], what: str) -> str:
    if proc.returncode != 0:
        raise RuntimeError(f"{what} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


def _docker(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], text=True, capture_output=True, timeout=timeout, check=False
    )


def _wait_for_container(
    container_id: str, timeout_s: float, required_running: tuple[str, ...] = ()
) -> int:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        proc = _docker("inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", container_id)
        if proc.returncode != 0:
            raise RuntimeError(f"docker inspect failed: {proc.stderr.strip()}")
        running, exit_code = proc.stdout.strip().split()
        if running == "false":
            return int(exit_code)
        for required in required_running:
            state = _require_ok(_docker("inspect", "-f", "{{.State.Running}}", required), "inspect")
            if state != "true":
                raise RuntimeError(f"required container {required[:12]} stopped before sender")
        time.sleep(0.25)
    raise TimeoutError(f"container {container_id[:12]} did not exit within {timeout_s:.0f}s")


def _copy_from_container(container_id: str, source: str, target: Path) -> None:
    proc = _docker("cp", f"{container_id}:{source}", str(target), timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"docker cp {source} failed: {proc.stderr.strip()}")


def _logs(container_id: str) -> str:
    proc = _docker("logs", container_id, timeout=60)
    _require_ok(proc, "docker logs")
    return (proc.stdout or "") + (proc.stderr or "")


def _parse_record(log_text: str, marker: str) -> dict[str, Any]:
    for line in log_text.splitlines():
        if line.startswith(marker):
            return json.loads(line[len(marker) :])
    raise RuntimeError(f"service did not emit a {marker.strip()} line")


def _parse_sender_record(log_text: str) -> dict[str, Any]:
    return _parse_record(log_text, "SENDER_RECORD ")


def _verify_isolation(containers: list[dict[str, Any]]) -> dict[str, Any]:
    receiver = containers[0]
    names = {item.split("=", 1)[0] for item in receiver["Config"]["Env"]}
    leaked = names & {"CHANNEL_BITS", "CHANNEL_MESSAGE"}
    if leaked:
        raise RuntimeError(f"payload environment leaked to receiver: {sorted(leaked)}")
    if any(mount.get("Type") == "bind" for item in containers for mount in item["Mounts"]):
        raise RuntimeError("host bind mounts are not permitted in the isolated lab")
    receiver_sources = {mount["Source"] for mount in receiver["Mounts"] if mount.get("Source")}
    other_sources = {
        mount["Source"]
        for item in containers[1:]
        for mount in item["Mounts"]
        if mount.get("Source")
    }
    if any(
        left == right
        or left.startswith(right.rstrip("/") + "/")
        or right.startswith(left.rstrip("/") + "/")
        for left in receiver_sources
        for right in other_sources
    ):
        raise RuntimeError("receiver shares a filesystem mount with another role")
    return {"receiver_payload_env_absent": True, "receiver_mounts_private": True}


def _channel_env(
    scenario: dict[str, Any], repeat: int, defaults: dict[str, Any]
) -> tuple[dict[str, str], str | None]:
    channel = {
        **ChannelConfig(calibration_pairs=4).as_dict(),
        **defaults,
        **scenario.get("channel", {}),
    }
    mode = scenario.get("mode", "raw")
    env = os.environ.copy()
    env["COMPOSE_PROFILES"] = ""
    env["COMPOSE_PROJECT_NAME"] = "timing-channel-lab-" + uuid.uuid4().hex[:12]
    env.update(
        {
            "CHANNEL_HOST": str(channel.get("host", "resource")),
            "CHANNEL_PORT": str(channel.get("port", 8080)),
            "CHANNEL_BODY_BYTES": str(channel.get("body_bytes", 64)),
            "CHANNEL_BIT0_MS": str(channel["bit0_ms"]),
            "CHANNEL_BIT1_MS": str(channel["bit1_ms"]),
            "CHANNEL_SYNC_MS": str(channel["sync_ms"]),
            "CHANNEL_GAP_MS": str(channel["gap_ms"]),
            "CHANNEL_POLL_MS": str(channel["poll_ms"]),
            "CHANNEL_BIT_THRESHOLD_MS": str(channel["bit_threshold_ms"]),
            "CHANNEL_SYNC_THRESHOLD_MS": str(channel["sync_threshold_ms"]),
            "CHANNEL_EVENT_THRESHOLD_MS": str(channel["event_threshold_ms"]),
            "CHANNEL_TIMEOUT_S": str(channel.get("timeout_s", 5)),
            "CHANNEL_CALIBRATION_PAIRS": str(channel.get("calibration_pairs", 4)),
            "CHANNEL_LEAD_IN_MS": str(channel.get("lead_in_ms", 3000)),
            "CHANNEL_NOISE_SEED": str(int(scenario.get("noise_seed", 20260826)) + repeat),
            "CHANNEL_MODE": str(mode),
            "CHANNEL_MESSAGE": str(scenario.get("message", "")),
        }
    )
    bits: str | None = None
    if mode == "raw":
        count = scenario.get("bits", 64)
        if type(count) is not int or not 1 <= count <= 65535:
            raise ValueError("raw bit count must be in 1..65535")
        rng = random.Random(int(scenario.get("seed", 0)) + repeat)
        bits = "".join(str(rng.randint(0, 1)) for _ in range(count))
        env["CHANNEL_BITS"] = bits
    else:
        env["CHANNEL_BITS"] = ""
    return env, bits


def _source_hashes() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(
            [
                *SRC.rglob("*.py"),
                *(ROOT / "scripts").glob("*.py"),
                *(ROOT / "tests").glob("*.py"),
                COMPOSE,
                ROOT / "Dockerfile",
                ROOT / ".dockerignore",
                ROOT / "experiments.json",
                ROOT / "pyproject.toml",
                ROOT / "LICENSE",
                ROOT / "README.md",
            ]
        )
    }


def _image_runtime_info(resource_id: str) -> dict[str, Any]:
    script = (
        "import hashlib,json,pathlib,platform; root=pathlib.Path('/app'); "
        "print(json.dumps({'python_version':'Python '+platform.python_version(),"
        "'source_sha256':{p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()"
        " for p in (root/'src').rglob('*.py')}}))"
    )
    return json.loads(
        _require_ok(_docker("exec", resource_id, "python", "-c", script), "image source inspection")
    )


def _discover_containers(env: dict[str, str], container_ids: dict[str, str], noise: bool) -> None:
    for role in ("receiver", "sender", "resource", *(("noise",) if noise else ())):
        if role not in container_ids:
            value = _require_ok(_compose(env, "ps", "-aq", role), f"{role} id")
            if value:
                container_ids[role] = value


def completed_analyses(results: Path) -> list[dict[str, Any]]:
    analyses = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in results.glob("*/analysis.json")
        if not (path.parent / "error.txt").exists()
    ]
    return sorted(analyses, key=lambda row: (str(row.get("scenario")), str(row.get("run"))))


def _sender_timeout(env: dict[str, str]) -> float:
    config = ChannelConfig(
        **{
            field: converter(env[key])
            for field, key, converter in (
                ("host", "CHANNEL_HOST", str),
                ("port", "CHANNEL_PORT", int),
                ("body_bytes", "CHANNEL_BODY_BYTES", int),
                ("bit0_ms", "CHANNEL_BIT0_MS", float),
                ("bit1_ms", "CHANNEL_BIT1_MS", float),
                ("sync_ms", "CHANNEL_SYNC_MS", float),
                ("gap_ms", "CHANNEL_GAP_MS", float),
                ("poll_ms", "CHANNEL_POLL_MS", float),
                ("event_threshold_ms", "CHANNEL_EVENT_THRESHOLD_MS", float),
                ("bit_threshold_ms", "CHANNEL_BIT_THRESHOLD_MS", float),
                ("sync_threshold_ms", "CHANNEL_SYNC_THRESHOLD_MS", float),
                ("timeout_s", "CHANNEL_TIMEOUT_S", float),
                ("calibration_pairs", "CHANNEL_CALIBRATION_PAIRS", int),
            )
        }
    )
    if env["CHANNEL_MODE"] == "framed":
        symbols = frame_symbols(env["CHANNEL_MESSAGE"].encode(), config.calibration_pairs)
    elif env["CHANNEL_MODE"] == "raw":
        bits = [int(bit) for bit in env["CHANNEL_BITS"]]
        if not bits or len(bits) > 65535:
            raise ValueError("raw bit count must be in 1..65535")
        symbols = raw_symbols(bits, config.calibration_pairs)
    else:
        raise ValueError("mode must be framed or raw")
    # Generous deadline, not a receiver capture length. Completion controls capture.
    nominal = sum(config.duration_ms(symbol) + config.gap_ms for symbol in symbols) / 1000
    lead_in = float(env["CHANNEL_LEAD_IN_MS"]) / 1000
    if not math.isfinite(lead_in) or lead_in < 0:
        raise ValueError("lead-in must be finite and nonnegative")
    return lead_in + nominal * 2 + 30


def _run_once(
    scenario: dict[str, Any],
    defaults: dict[str, Any],
    repeat: int,
    run_dir: Path,
    *,
    image: str | None = None,
    source_hashes: dict[str, str] | None = None,
    source_snapshot: str | None = None,
) -> dict[str, Any]:
    env, bits = _channel_env(scenario, repeat, defaults)
    if image is not None:
        env["CHANNEL_IMAGE"] = image
    source_hashes = source_hashes if source_hashes is not None else _source_hashes()
    sender_timeout = _sender_timeout(env)
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "scenario": scenario["name"],
                "repeat": repeat,
                "bits_sent": bits,
                "scenario_definition": scenario,
                "defaults": defaults,
                "compose_project": env["COMPOSE_PROJECT_NAME"],
                "source_snapshot": source_snapshot,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    container_ids: dict[str, str] = {}
    try:
        up_args = ["up", "-d", "--remove-orphans"]
        if scenario.get("noise"):
            up_args = ["--profile", "noise", *up_args]
        _require_ok(_compose(env, *up_args, timeout=300), "compose up")

        _discover_containers(env, container_ids, bool(scenario.get("noise")))
        receiver_id = container_ids["receiver"]
        sender_id = container_ids["sender"]
        resource_id = container_ids["resource"]
        noise_id = container_ids.get("noise", "")
        if scenario.get("noise") and not noise_id:
            raise RuntimeError("noise service was not created")
        inspect = json.loads(_require_ok(_docker("inspect", *container_ids.values()), "inspect"))
        isolation = _verify_isolation(inspect)
        (run_dir / "isolation.json").write_text(json.dumps(isolation, indent=2) + "\n")
        image_id = inspect[0]["Image"]
        if any(container["Image"] != image_id for container in inspect):
            raise RuntimeError("container roles do not use the same image")
        runtime = _image_runtime_info(resource_id)
        if runtime["source_sha256"] != {
            name: digest for name, digest in source_hashes.items() if name.startswith("src/")
        }:
            raise RuntimeError(
                "container image source does not match host source; rebuild the image"
            )
        image = json.loads(_require_ok(_docker("image", "inspect", image_id), "image inspect"))[0]
        (run_dir / "environment.json").write_text(
            json.dumps(
                {
                    "docker_server": _require_ok(
                        _docker("version", "--format", "{{.Server.Version}}"), "docker version"
                    ),
                    "image_id": image_id,
                    "image_created": image["Created"],
                    "python_version": runtime["python_version"],
                    "image_source_sha256": runtime["source_sha256"],
                    "source_sha256": source_hashes,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        sender_exit = _wait_for_container(
            sender_id, sender_timeout, required_running=(receiver_id, resource_id)
        )
        stop_timeout = str(math.ceil(float(env["CHANNEL_TIMEOUT_S"])) + 2)
        noise_events = 0
        if noise_id:
            _require_ok(
                _compose(env, "--profile", "noise", "stop", "-t", stop_timeout, "noise"),
                "stop noise",
            )
        _require_ok(_compose(env, "stop", "-t", stop_timeout, "receiver"), "stop receiver")
        exit_code = _wait_for_container(receiver_id, timeout_s=10)

        sender_log = _logs(sender_id)
        (run_dir / "resource.log").write_text(_logs(resource_id), encoding="utf-8")
        (run_dir / "sender.log").write_text(sender_log, encoding="utf-8")
        (run_dir / "receiver.log").write_text(_logs(receiver_id), encoding="utf-8")
        if noise_id:
            noise_log = _logs(noise_id)
            (run_dir / "noise.log").write_text(noise_log, encoding="utf-8")
            noise_record = _parse_record(noise_log, "NOISE_RECORD ")
            noise_events = len(noise_record["events"])
            if _wait_for_container(noise_id, 10) != 0 or not noise_record["events"]:
                raise RuntimeError("noise service produced no contention; see noise.log")
        if sender_exit != 0:
            raise RuntimeError(f"sender exited with {sender_exit}; see sender.log")
        if exit_code != 0:
            raise RuntimeError(f"receiver exited with {exit_code}; see {run_dir / 'receiver.log'}")

        _copy_from_container(receiver_id, "/run/channel/receiver.json", run_dir / "receiver.json")
        sender_record = _parse_sender_record(sender_log)
        (run_dir / "sender.json").write_text(
            json.dumps(sender_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        analysis = analyze_run(run_dir, write=False)
        requests = analysis["requests"]
        if (
            not requests["body_hashes_complete"]
            or requests["unique_body_sha256"] != 1
            or requests["request_bytes"] != [int(env["CHANNEL_BODY_BYTES"])]
        ):
            raise RuntimeError("work requests did not have constant fixed-size bodies")
        if requests["work_requests"] != len(sender_record["symbols"]) + noise_events:
            raise RuntimeError(
                "resource log request count differs from completed sender/noise work"
            )
        if not analysis["receiver_decoded_independently"]:
            raise RuntimeError("receiver did not record an independent timing decode")
        write_json_atomic(run_dir / "analysis.json", analysis)
        (run_dir / "summary.md").write_text(_format_summary(analysis), encoding="utf-8")
        return analysis
    finally:
        original_error = sys.exc_info()[1]
        if original_error is not None:
            try:
                _discover_containers(env, container_ids, bool(scenario.get("noise")))
            except Exception as exc:
                print(f"could not discover failed-run containers: {exc}", file=sys.stderr)
        if original_error is not None and container_ids:
            # Preserve the partial trace before down removes the private volume.
            stop_timeout = str(math.ceil(float(env["CHANNEL_TIMEOUT_S"])) + 2)
            roles = [role for role in ("sender", "noise", "receiver") if role in container_ids]
            try:
                _require_ok(
                    _compose(env, "--profile", "noise", "stop", "-t", stop_timeout, *roles),
                    "stop failed run",
                )
                if "receiver" in container_ids and not (run_dir / "receiver.json").exists():
                    _copy_from_container(
                        container_ids["receiver"],
                        "/run/channel/receiver.json",
                        run_dir / "receiver.json",
                    )
            except Exception as exc:
                print(f"could not preserve partial receiver trace: {exc}", file=sys.stderr)
        for role, container_id in container_ids.items():
            try:
                (run_dir / f"{role}.log").write_text(_logs(container_id), encoding="utf-8")
            except Exception as exc:
                print(f"could not collect {role} log: {exc}", file=sys.stderr)
        try:
            _require_ok(
                _compose(env, "--profile", "noise", "down", "-v", "--remove-orphans"),
                "compose cleanup",
            )
        except Exception as cleanup_error:
            if original_error is None:
                raise
            original_error.add_note(f"Cleanup also failed: {cleanup_error}")
            print(f"cleanup also failed: {cleanup_error}", file=sys.stderr)


def _aggregate(scenarios: list[dict[str, Any]], analyses: list[dict[str, Any]]) -> str:
    lines = [
        "# Timing channel lab results",
        "",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
        "The sender delays the whole body; request-content evidence is recorded per run. "
        "Error rate is normalized Levenshtein distance (including insertions/deletions). "
        "Data rate includes the framed length field; calibration and sync consume time.",
        "",
        "| Scenario | Run | Pairs | Bits | Decoded | Edit rate | Exact | Log Δ rate | "
        "Log read rate | Threshold (ms) | 0 mean (ms) | 1 mean (ms) | bit/s |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for analysis in analyses:
        channel = analysis["channel"]
        timing = analysis["timing"]
        zero = timing.get("zero_rtt_ms", {}).get("mean")
        one = timing.get("one_rtt_ms", {}).get("mean")
        log_ber = analysis["log_decode"]["ber"]
        read_ber = analysis["log_decode"].get("read_ms_ber")
        rate = analysis.get("throughput_bps")
        lines.append(
            f"| {analysis.get('scenario')} | {analysis['run']} | "
            f"{analysis['config'].get('calibration_pairs', 0)} | {channel['expected_bits']} | "
            f"{channel['decoded_bits']} | {channel['bit_error_rate']:.3f} | "
            f"{'yes' if channel['frame_ok'] else 'no'} | "
            f"{'n/a' if log_ber is None else f'{log_ber:.3f}'} | "
            f"{'n/a' if read_ber is None else f'{read_ber:.3f}'} | "
            f"{timing.get('bit0_threshold_ms', analysis['config']['bit_threshold_ms']):.3f} | "
            f"{'n/a' if zero is None else f'{zero:.1f}'} | "
            f"{'n/a' if one is None else f'{one:.1f}'} | "
            f"{'n/a' if rate is None else f'{rate:.2f}'} |"
        )

    lines.extend(["", "## Per-scenario mean normalized edit distance", ""])
    by_scenario: dict[str, list[float]] = {}
    for analysis in analyses:
        by_scenario.setdefault(str(analysis.get("scenario")), []).append(
            float(analysis["channel"]["bit_error_rate"])
        )
    for name, values in by_scenario.items():
        lines.append(f"- `{name}`: {sum(values) / len(values):.3f} ({len(values)} run(s))")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the docker timing-channel experiments")
    parser.add_argument("--config", type=Path, default=ROOT / "experiments.json")
    parser.add_argument("--only", default="", help="Comma-separated scenario names")
    parser.add_argument("--repeats", type=int, default=0, help="Override repeat count")
    parser.add_argument("--results", type=Path, default=RESULTS)
    args = parser.parse_args()
    if args.repeats < 0:
        parser.error("--repeats cannot be negative")

    spec = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or not isinstance(spec.get("scenarios"), list):
        parser.error("configuration must contain a scenarios list")
    if any(not isinstance(scenario, dict) for scenario in spec["scenarios"]):
        parser.error("each scenario must be an object")
    names = [scenario.get("name") for scenario in spec["scenarios"]]
    if any(
        not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
        for name in names
    ) or len(set(names)) != len(names):
        parser.error("scenario names must be unique safe directory names")
    defaults = spec.get("defaults", {})
    selected = {name.strip() for name in args.only.split(",") if name.strip()}
    unknown = selected - {scenario["name"] for scenario in spec["scenarios"]}
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(sorted(unknown))}")
    scenarios = [
        scenario for scenario in spec["scenarios"] if not selected or scenario["name"] in selected
    ]
    if not scenarios:
        raise SystemExit("no scenarios selected")
    try:
        for scenario in scenarios:
            repeats = args.repeats or scenario.get("repeats", 1)
            if type(repeats) is not int or repeats <= 0:
                raise ValueError("scenario repeats must be a positive integer")
            env, _ = _channel_env(scenario, 0, defaults)
            _sender_timeout(env)
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))

    probe = _docker("info", "--format", "{{.ServerVersion}}", timeout=60)
    if probe.returncode != 0:
        raise SystemExit(
            "docker is not available; start Docker Desktop and retry:\n" + probe.stderr
        )
    print(f"docker server {probe.stdout.strip()}", flush=True)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    build_env = os.environ.copy()
    build_env["CHANNEL_IMAGE"] = "timing-channel-lab:run-" + uuid.uuid4().hex[:12]
    source_hashes = _source_hashes()
    _require_ok(
        _compose(build_env, "--profile", "noise", "build", timeout=600),
        "compose build",
    )
    args.results.mkdir(parents=True, exist_ok=True)
    snapshot_name = f"source-{stamp}.zip"
    with ZipFile(args.results / snapshot_name, "w", ZIP_DEFLATED) as snapshot:
        for name, digest in source_hashes.items():
            content = (ROOT / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise RuntimeError("source changed during build; retry with a stable source tree")
            snapshot.writestr(name, content)
    analyses: list[dict[str, Any]] = []
    failures: list[str] = []

    for scenario in scenarios:
        repeats = args.repeats or int(scenario.get("repeats", 1))
        for repeat in range(repeats):
            name = f"{stamp}-{scenario['name']}-r{repeat}"
            run_dir = args.results / name
            print(f"[{name}] starting", flush=True)
            try:
                analysis = _run_once(
                    scenario,
                    defaults,
                    repeat,
                    run_dir,
                    image=build_env["CHANNEL_IMAGE"],
                    source_hashes=source_hashes,
                    source_snapshot=snapshot_name,
                )
                analyses.append(analysis)
                channel = analysis["channel"]
                print(
                    f"[{name}] bits={channel['decoded_bits']}/{channel['expected_bits']} "
                    f"ber={channel['bit_error_rate']:.3f} frame_ok={channel['frame_ok']}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - a failed run should not stop the matrix
                failures.append(f"{name}: {exc}")
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
                print(f"[{name}] FAILED: {exc}", flush=True)

    completed = completed_analyses(args.results)
    if completed:
        (args.results / "RESULTS.md").write_text(_aggregate(scenarios, completed), encoding="utf-8")
        write_json_atomic(args.results / "aggregate.json", completed)
    if failures:
        (args.results / "FAILURES.txt").write_text("\n\n".join(failures) + "\n", encoding="utf-8")
        print(f"{len(failures)} run(s) failed; see {args.results / 'FAILURES.txt'}", flush=True)
        raise SystemExit(1)
    (args.results / "FAILURES.txt").unlink(missing_ok=True)
    print(f"wrote {args.results / 'RESULTS.md'}", flush=True)


if __name__ == "__main__":
    main()
