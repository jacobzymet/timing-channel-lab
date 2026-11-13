import json

import pytest
import rebuild_results
import run_experiment as runner

from timingchannel.analysis import analyze_run
from timingchannel.codec import bytes_to_bits, frame_symbols
from timingchannel.config import ChannelConfig


@pytest.fixture
def encoded_run(tmp_path):
    tmp_path = tmp_path / "run"
    tmp_path.mkdir()
    config = ChannelConfig(calibration_pairs=4)
    payload = b"OK"
    bits = bytes_to_bits(len(payload).to_bytes(2, "big") + payload)
    receiver = {
        "config": config.as_dict(),
        "decoded_bits": bits,
        "decode_error": None,
        "bit_threshold_ms": 161.5,
        "samples": [
            {"t_ms": i * 1000, "rtt_ms": config.duration_ms(symbol) + 1.5}
            for i, symbol in enumerate(frame_symbols(payload, 4))
        ],
    }
    sender = {
        "config": config.as_dict(),
        "data_bits": bits,
        "mode": "framed",
        "payload_hex": payload.hex(),
        "payload_bytes": len(payload),
        "send_span_ms": 1000,
    }
    (tmp_path / "receiver.json").write_text(json.dumps(receiver))
    (tmp_path / "sender.json").write_text(json.dumps(sender))
    (tmp_path / "config.json").write_text('{"scenario":"synthetic"}')
    return tmp_path


def test_ground_truth_changes_scores_but_cannot_change_decoded_payload(encoded_run):
    tmp_path = encoded_run
    sender = json.loads((tmp_path / "sender.json").read_text())
    bits = sender["data_bits"]
    correct = analyze_run(tmp_path)
    assert correct["channel"]["frame_ok"]
    assert correct["throughput_bps"] == 32
    assert correct["payload_throughput_bps"] == 16
    sender.update(data_bits=[1 - bit for bit in bits], payload_hex="0000")
    (tmp_path / "sender.json").write_text(json.dumps(sender))
    wrong = analyze_run(tmp_path)
    assert wrong["channel"]["decoded_payload_text"] == "OK"
    assert not wrong["channel"]["frame_ok"]
    assert wrong["channel"]["bit_error_rate"] > 0
    assert wrong["timing"]["bit0_threshold_ms"] == correct["timing"]["bit0_threshold_ms"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("decoded_bits", [1]),
        ("decode_error", "lost sync"),
        ("bit_threshold_ms", 165),
    ],
)
def test_independent_decode_checks_all_fields(encoded_run, field, value):
    path = encoded_run / "receiver.json"
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match=field):
        analyze_run(encoded_run)


def test_analysis_preview_does_not_publish_unvalidated_results(encoded_run):
    assert analyze_run(encoded_run, write=False)["channel"]["frame_ok"]
    assert not (encoded_run / "analysis.json").exists()


def test_rebuild_recovers_missing_analysis_and_excludes_failed_runs(encoded_run, monkeypatch):
    monkeypatch.setattr(
        rebuild_results.sys,
        "argv",
        [
            "rebuild",
            "--results",
            str(encoded_run.parent),
            "--reanalyze",
        ],
    )
    rebuild_results.main()
    assert (encoded_run / "analysis.json").exists()
    assert len(json.loads((encoded_run.parent / "aggregate.json").read_text())) == 1
    (encoded_run / "error.txt").write_text("failed validation")
    assert runner.completed_analyses(encoded_run.parent) == []
    with pytest.raises(SystemExit, match="no analysis"):
        rebuild_results.main()


def test_missing_rate_can_be_rendered(encoded_run):
    row = analyze_run(encoded_run, write=False)
    row["throughput_bps"] = None
    assert "n/a" in runner._aggregate([], [row])


def test_main_uses_unique_images_and_preserves_existing_reports(encoded_run, monkeypatch, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"scenarios": [{"name": "fresh", "mode": "framed", "message": "OK"}]})
    )
    output = tmp_path / "reports"
    old_dir = output / "earlier"
    old_dir.mkdir(parents=True)
    row = analyze_run(encoded_run, write=False)
    row.update(run="earlier", scenario="earlier")
    (old_dir / "analysis.json").write_text(json.dumps(row))
    builds, runs = [], []

    def compose(env, *args, **kwargs):
        builds.append(env["CHANNEL_IMAGE"])
        return runner.subprocess.CompletedProcess([], 0, "", "")

    def once(scenario, defaults, repeat, run_dir, **kwargs):
        runs.append(kwargs["image"])
        run_dir.mkdir()
        record = {**row, "run": run_dir.name, "scenario": scenario["name"]}
        (run_dir / "analysis.json").write_text(json.dumps(record))
        return record

    monkeypatch.setattr(runner, "_compose", compose)
    monkeypatch.setattr(
        runner, "_docker", lambda *a, **k: runner.subprocess.CompletedProcess([], 0, "test", "")
    )
    monkeypatch.setattr(runner, "_run_once", once)
    monkeypatch.setattr(
        runner.sys, "argv", ["runner", "--config", str(spec), "--results", str(output)]
    )
    runner.main()
    runner.main()
    assert len(set(builds)) == 2 and builds == runs
    assert len(json.loads((output / "aggregate.json").read_text())) == 3
    assert len(list(output.glob("source-*.zip"))) == 2
