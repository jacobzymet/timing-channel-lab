"""Probing receiver: sample /ping latency and save the raw trace."""

from __future__ import annotations

import argparse
import math
import os
import signal
import threading
import time
from pathlib import Path

from timingchannel.codec import Sample, read_frame
from timingchannel.config import ChannelConfig
from timingchannel.wire import ping_once, utc_now_iso, wait_for_resource, write_json_atomic


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sample latency of the shared resource")
    parser.add_argument(
        "--duration", type=float, default=float(os.environ.get("CHANNEL_DURATION_S", "30"))
    )
    parser.add_argument(
        "--output", default=os.environ.get("CHANNEL_RECEIVER_OUTPUT", "/run/channel/receiver.json")
    )
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    return parser.parse_args()


def sample_latency(
    config: ChannelConfig, duration: float, stop: threading.Event, start: float
) -> list[Sample]:
    """Sample at the requested cadence; waits remain interruptible even when long."""
    deadline = start + duration if duration else math.inf
    next_sample = start
    samples: list[Sample] = []
    while not stop.is_set():
        now = time.monotonic()
        delay = min(next_sample, deadline) - now
        if delay > 0 and stop.wait(delay):
            break
        sampled_at = time.monotonic()
        if stop.is_set() or sampled_at >= deadline:
            break
        rtt_ms, failed = ping_once(config.host, config.port, config.timeout_s)
        samples.append(
            Sample(
                t_ms=round((sampled_at - start) * 1000, 3),
                rtt_ms=round(rtt_ms, 3),
                timeout=failed,
            )
        )
        next_sample += config.interval_s
        if next_sample < time.monotonic() - config.interval_s:
            next_sample = time.monotonic()
    return samples


def main() -> None:
    args = parse_args()
    config = ChannelConfig.from_env()
    if not math.isfinite(args.duration) or args.duration < 0:
        raise ValueError("duration must be finite and nonnegative (0 means until stopped)")
    forbidden = {"CHANNEL_MESSAGE", "CHANNEL_BITS"}.intersection(os.environ)
    if forbidden:
        raise ValueError(f"receiver must not receive payload variables: {sorted(forbidden)}")
    ready_path = Path(os.environ.get("CHANNEL_READY_FILE", "/tmp/receiver-ready"))
    ready_path.unlink(missing_ok=True)
    wait_for_resource(config.host, config.port, args.startup_timeout)

    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    start = time.monotonic()
    started_at = utc_now_iso()

    print(
        f"receiver sampling: {config.host}:{config.port} "
        f"for {args.duration:.1f}s every {config.poll_ms:.1f}ms",
        flush=True,
    )
    # Private health marker: never mounted into another role.
    ready_path.touch()
    try:
        samples = sample_latency(config, args.duration, stop, start)
    finally:
        ready_path.unlink(missing_ok=True)

    decoded = read_frame(samples, config)
    capture = {
        "version": 2,
        "role": "receiver",
        "started_at": started_at,
        "duration_s": args.duration,
        "config": config.as_dict(),
        "decoded_bits": list(decoded.bits),
        "decode_error": decoded.error,
        "bit_threshold_ms": decoded.bit_threshold_ms,
        "samples": [
            {"t_ms": sample.t_ms, "rtt_ms": sample.rtt_ms, "timeout": sample.timeout}
            for sample in samples
        ],
    }
    write_json_atomic(args.output, capture)

    rtts = [sample.rtt_ms for sample in samples if not sample.timeout]
    events = [rtt for rtt in rtts if rtt >= config.event_threshold_ms]
    print(
        f"receiver done: {len(samples)} samples, {len(events)} events, "
        f"max_rtt={max(rtts, default=0.0):.1f}ms -> {args.output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
