"""Background contention generator for the noisy-channel trial.

The generator uses the same /work endpoint as the sender, so it looks like
ordinary administrative load from the resource's point of view.  It creates
false pulse events and can stretch real pulses when the two overlap.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import signal
import threading
import time

from timingchannel.config import ChannelConfig
from timingchannel.wire import post_with_hold, wait_for_resource


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Random background load on the shared resource")
    parser.add_argument(
        "--duration", type=float, default=float(os.environ.get("CHANNEL_DURATION_S", "30"))
    )
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    parser.add_argument(
        "--lead-in-ms", type=float, default=float(os.environ.get("CHANNEL_LEAD_IN_MS", "3000"))
    )
    parser.add_argument("--min-interval-ms", type=float, default=700.0)
    parser.add_argument("--max-interval-ms", type=float, default=2000.0)
    parser.add_argument("--min-hold-ms", type=float, default=40.0)
    parser.add_argument("--max-hold-ms", type=float, default=300.0)
    parser.add_argument(
        "--seed", type=int, default=int(os.environ.get("CHANNEL_NOISE_SEED", "20260826"))
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ChannelConfig.from_env()
    for name in (
        "duration",
        "lead_in_ms",
        "min_interval_ms",
        "max_interval_ms",
        "min_hold_ms",
        "max_hold_ms",
    ):
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    if args.min_interval_ms <= 0 or args.min_interval_ms > args.max_interval_ms:
        raise ValueError("expected 0 < min_interval_ms <= max_interval_ms")
    if args.min_hold_ms > args.max_hold_ms:
        raise ValueError("expected min_hold_ms <= max_hold_ms")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    rng = random.Random(args.seed)
    wait_for_resource(config.host, config.port, args.startup_timeout)
    if args.lead_in_ms:
        stop.wait(args.lead_in_ms / 1000.0)

    started = time.monotonic()
    deadline = started + args.duration if args.duration else math.inf
    events: list[dict[str, float]] = []
    print(
        f"noise starting: interval={args.min_interval_ms:.0f}-{args.max_interval_ms:.0f}ms "
        f"hold={args.min_hold_ms:.0f}-{args.max_hold_ms:.0f}ms seed={args.seed}",
        flush=True,
    )

    while not stop.is_set() and time.monotonic() < deadline:
        delay_ms = rng.uniform(args.min_interval_ms, args.max_interval_ms)
        stop.wait(min(delay_ms, max(0.0, (deadline - time.monotonic()) * 1000.0)) / 1000.0)
        if stop.is_set() or time.monotonic() >= deadline:
            break
        hold_ms = rng.uniform(args.min_hold_ms, args.max_hold_ms)
        started_ms = (time.monotonic() - started) * 1000.0
        post_with_hold(
            config.host, config.port, b"x" * config.body_bytes, hold_ms, config.timeout_s
        )
        events.append({"t_ms": round(started_ms, 3), "hold_ms": round(hold_ms, 3)})

    print(f"noise done: {len(events)} contention pulses", flush=True)
    print("NOISE_RECORD " + json.dumps({"seed": args.seed, "events": events}), flush=True)


if __name__ == "__main__":
    main()
