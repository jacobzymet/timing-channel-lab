"""Host-side analysis: decode the receiver trace and the resource log."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from timingchannel.codec import (
    FrameRead,
    Sample,
    bit_error_rate,
    classify_pulses,
    decode_framed,
    read_frame,
)
from timingchannel.config import ChannelConfig
from timingchannel.wire import read_json, utc_now_iso, write_json_atomic


def parse_work_events(path: Path) -> list[dict[str, Any]]:
    """Recover structured /work records from ``docker compose logs`` output."""
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        brace = line.find("{")
        if brace < 0:
            continue
        try:
            record = json.loads(line[brace:])
        except json.JSONDecodeError:
            continue
        if (
            isinstance(record, dict)
            and record.get("event") == "work"
            and record.get("path") == "/work"
        ):
            events.append(record)
    return events


def _log_decode(
    events: list[dict[str, Any]],
    config: ChannelConfig,
    expected_bits: list[int],
) -> dict[str, Any]:
    hashes = [event.get("sha256") for event in events]
    result: dict[str, Any] = {
        "requests": len(events),
        "unique_body_sha256": len({value for value in hashes if isinstance(value, str) and value}),
        "body_hashes_complete": bool(events)
        and all(
            isinstance(value, str)
            and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)
            for value in hashes
        ),
        "unique_paths": len({event.get("path") for event in events}),
        "request_bytes": sorted({int(event.get("bytes", 0)) for event in events}),
        "bits": [],
        "ber": None,
        "read_ms_bits": [],
        "read_ms_ber": None,
        "read_ms_preamble_ms": None,
        "preamble_ms": None,
        "error": None,
        "bit_threshold_ms": None,
        "read_ms_bit_threshold_ms": None,
        "read_ms_error": None,
        "timestamp_postamble_inferred": True,
    }
    try:
        starts = [float(event["t_start_ms"]) for event in events]
        reads = [float(event["read_ms"]) for event in events]
        if any(not math.isfinite(value) or value < 0 for value in [*starts, *reads]):
            raise ValueError("log times must be finite and nonnegative")
        if any(b <= a for a, b in zip(starts, starts[1:], strict=False)):
            raise ValueError("request starts must be strictly increasing")
    except (KeyError, TypeError, ValueError) as exc:
        result["error"] = result["read_ms_error"] = f"invalid work log: {exc}"
        return result
    if len(events) < 2:
        result["error"] = result["read_ms_error"] = "fewer than two framing requests"
        return result
    expected_requests = len(expected_bits) + 2 * config.calibration_pairs + 2
    count_error = (
        f"expected {expected_requests} /work requests, saw {len(starts)}"
        if len(starts) != expected_requests
        else None
    )

    deltas = [right - left for left, right in zip(starts, starts[1:], strict=False)]
    result["preamble_ms"] = round(deltas[0] - config.gap_ms, 3)
    widths = [delta - config.gap_ms for delta in deltas]
    observed, threshold, error = _decode_log_widths(widths[1:], widths[0], None, config)
    result["bit_threshold_ms"] = round(threshold, 6)
    result["bits"] = list(observed)
    if error is None and count_error is None:
        result["ber"] = round(bit_error_rate(expected_bits, observed), 6)
    result["error"] = error or count_error

    # A service that logs request duration exposes the pulse width directly.
    # This is a stronger observer than request-start deltas: no cadence
    # reconstruction is needed, just a threshold on each /work record.
    read_bits, read_threshold, read_error = _decode_log_widths(
        reads[1:-1], reads[0], reads[-1], config
    )
    result["read_ms_bits"] = list(read_bits)
    result["read_ms_bit_threshold_ms"] = round(read_threshold, 6)
    if read_error is None and count_error is None:
        result["read_ms_ber"] = round(bit_error_rate(expected_bits, read_bits), 6)
    result["read_ms_error"] = read_error or count_error
    result["read_ms_preamble_ms"] = round(float(events[0]["read_ms"]), 3)
    return result


def _decode_log_widths(
    widths: list[float], preamble: float, postamble: float | None, config: ChannelConfig
) -> tuple[tuple[int, ...], float, str | None]:
    # The timestamp-only view cannot measure the final request without a later
    # arrival. Its last record is the inferred end of the isolated capture.
    if preamble < config.sync_threshold_ms:
        return (), config.bit_threshold_ms, "preamble not observed in work log"
    if postamble is not None and postamble < config.sync_threshold_ms:
        return (), config.bit_threshold_ms, "postamble not observed in work log"
    if any(not config.event_threshold_ms <= value < config.sync_threshold_ms for value in widths):
        return (), config.bit_threshold_ms, "unexpected sync or invalid pulse in work log"
    return classify_pulses(widths, config)


def _timing_summary(
    frame: FrameRead,
    expected_bits: list[int],
    config: ChannelConfig,
) -> dict[str, Any]:
    if frame.preamble is None:
        return {}
    data_events = frame.data_events
    summary: dict[str, Any] = {
        "bit0_threshold_ms": round(frame.bit_threshold_ms or config.bit_threshold_ms, 6),
        "configured_bit_threshold_ms": config.bit_threshold_ms,
        "calibration_events": len(frame.calibration_events),
        "sync_threshold_ms": config.sync_threshold_ms,
        "preamble_rtt_ms": round(frame.preamble.rtt_ms, 3),
        "postamble_rtt_ms": round(frame.postamble.rtt_ms, 3) if frame.postamble else None,
        "data_events": len(data_events),
    }
    if len(data_events) == len(expected_bits):
        zeros = [
            event.rtt_ms for event, bit in zip(data_events, expected_bits, strict=False) if bit == 0
        ]
        ones = [
            event.rtt_ms for event, bit in zip(data_events, expected_bits, strict=False) if bit == 1
        ]
        for label, values in (("zero", zeros), ("one", ones)):
            if values:
                summary[f"{label}_rtt_ms"] = {
                    "count": len(values),
                    "min": round(min(values), 3),
                    "mean": round(statistics.mean(values), 3),
                    "max": round(max(values), 3),
                }
    return summary


def analyze_run(run_dir: Path, *, write: bool = True) -> dict[str, Any]:
    receiver = read_json(run_dir / "receiver.json")
    sender = read_json(run_dir / "sender.json")
    run_config_path = run_dir / "config.json"
    run_config = read_json(run_config_path) if run_config_path.exists() else {}

    config = ChannelConfig(**receiver["config"])
    samples = [Sample(**sample) for sample in receiver["samples"]]
    expected_bits = [int(bit) for bit in sender["data_bits"]]
    frame = read_frame(samples, config)
    if "decoded_bits" in receiver:
        for field, value in (
            ("decoded_bits", list(frame.bits)),
            ("decode_error", frame.error),
            ("bit_threshold_ms", frame.bit_threshold_ms),
        ):
            if field not in receiver or receiver[field] != value:
                raise ValueError(f"receiver's independent {field} differs from host recomputation")
    if sender["config"] != receiver["config"]:
        raise ValueError("sender and receiver timing configurations differ")

    if sender["mode"] == "framed":
        framed = decode_framed(samples, config)
        payload = framed.payload
        frame_error = framed.error or frame.error
        payload_match = payload is not None and payload.hex() == sender["payload_hex"]
        decoded_payload_hex = payload.hex() if payload is not None else None
        decoded_payload_text = (
            payload.decode("utf-8", errors="replace") if payload is not None else None
        )
        frame_ok = bool(payload_match)
    else:
        frame_error = frame.error
        payload_match = None
        decoded_payload_hex = None
        decoded_payload_text = None
        frame_ok = frame.error is None and tuple(frame.bits) == tuple(expected_bits)

    ber = bit_error_rate(expected_bits, frame.bits)
    log_events = parse_work_events(run_dir / "resource.log")
    log_result = _log_decode(log_events, config, expected_bits)

    send_span_s = float(sender.get("send_span_ms", 0.0)) / 1000.0
    analysis: dict[str, Any] = {
        "version": 3,
        "run": run_dir.name,
        "scenario": run_config.get("scenario"),
        "generated_at": utc_now_iso(),
        "config": config.as_dict(),
        "channel": {
            "mode": sender["mode"],
            "expected_bits": len(expected_bits),
            "decoded_bits": len(frame.bits),
            "bit_error_rate": round(ber, 6),
            "error_metric": "levenshtein_distance / expected_bits",
            "hamming_error_rate": (
                round(
                    sum(a != b for a, b in zip(expected_bits, frame.bits, strict=True))
                    / len(expected_bits),
                    6,
                )
                if len(expected_bits) == len(frame.bits) and expected_bits
                else None
            ),
            "frame_ok": frame_ok,
            "payload_match": payload_match,
            "decoded_payload_hex": decoded_payload_hex,
            "decoded_payload_text": decoded_payload_text,
            "error": frame_error,
        },
        "samples": {
            "count": len(samples),
            "timeouts": sum(1 for sample in samples if sample.timeout),
            "events": len(frame.events),
        },
        "timing": _timing_summary(frame, expected_bits, config),
        "throughput_bps": round(len(expected_bits) / send_span_s, 3) if send_span_s else None,
        "payload_throughput_bps": (
            round(int(sender["payload_bytes"]) * 8 / send_span_s, 3)
            if send_span_s and sender["mode"] == "framed"
            else None
        ),
        "receiver_decoded_independently": "decoded_bits" in receiver,
        "requests": {
            "work_requests": log_result["requests"],
            "unique_body_sha256": log_result["unique_body_sha256"],
            "body_hashes_complete": log_result["body_hashes_complete"],
            "unique_paths": log_result["unique_paths"],
            "request_bytes": log_result["request_bytes"],
        },
        "log_decode": log_result,
    }
    if write:
        write_json_atomic(run_dir / "analysis.json", analysis)
    return analysis


def _format_summary(analysis: dict[str, Any]) -> str:
    channel = analysis["channel"]
    log = analysis["log_decode"]
    timing = analysis["timing"]
    read_ber = log["read_ms_ber"]
    lines = [
        f"# {analysis['run']}",
        "",
        f"- Scenario: `{analysis.get('scenario')}`",
        f"- Mode: `{channel['mode']}`",
        f"- Bits: {channel['decoded_bits']} decoded / {channel['expected_bits']} expected",
        f"- Normalized bit edit distance: {channel['bit_error_rate']:.3f}",
        f"- Exact recovery: {channel['frame_ok']}",
    ]
    if channel.get("decoded_payload_text") is not None:
        lines.append(f"- Payload: `{channel['decoded_payload_text']}`")
    if channel.get("error"):
        lines.append(f"- Receiver error: {channel['error']}")
    lines.extend(
        [
            f"- Work requests: {analysis['requests']['work_requests']}",
            f"- Unique request bodies: {analysis['requests']['unique_body_sha256']}",
            f"- Request sizes: {analysis['requests']['request_bytes']}",
            f"- Log timestamp BER: {log['ber'] if log['ber'] is not None else 'n/a'}",
            f"- Log read-time BER: {read_ber if read_ber is not None else 'n/a'}",
            f"- Data rate (length field included): {analysis['throughput_bps']} bit/s",
        ]
    )
    if analysis.get("payload_throughput_bps") is not None:
        lines.append(f"- Payload rate: {analysis['payload_throughput_bps']} bit/s")
    if timing:
        zero = timing.get("zero_rtt_ms")
        one = timing.get("one_rtt_ms")
        if zero and one:
            lines.append(
                f"- Observed zero/one means: {zero['mean']:.1f}ms / {one['mean']:.1f}ms "
                f"(threshold {timing['bit0_threshold_ms']:.1f}ms)"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Decode one timing-channel run")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    analysis = analyze_run(args.run_dir)
    (args.run_dir / "summary.md").write_text(_format_summary(analysis), encoding="utf-8")
    print(json.dumps(analysis, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
