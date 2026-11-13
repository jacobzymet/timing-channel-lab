"""Active sender: one paced request per symbol.

Every request is identical.  The only variable is how long the client waits
before sending the body, so a content monitor sees the same method,
path, headers and 64-byte body for zeros, ones and framing pulses.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time

from timingchannel.codec import bytes_to_bits, frame_symbols, raw_symbols
from timingchannel.config import ChannelConfig
from timingchannel.wire import post_with_hold, utc_now_iso, wait_for_resource


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send bits using request pacing")
    parser.add_argument(
        "--mode", choices=("framed", "raw"), default=os.environ.get("CHANNEL_MODE", "framed")
    )
    parser.add_argument("--message", default=os.environ.get("CHANNEL_MESSAGE", "HELLO"))
    parser.add_argument("--bits", default=os.environ.get("CHANNEL_BITS", ""))
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    parser.add_argument(
        "--lead-in-ms", type=float, default=float(os.environ.get("CHANNEL_LEAD_IN_MS", "3000"))
    )
    return parser.parse_args()


def _raw_bits(text: str) -> list[int]:
    text = "".join(text.split())
    if any(char not in "01" for char in text):
        raise ValueError("--bits must contain only 0, 1 or whitespace")
    bits = [int(char) for char in text]
    if not bits:
        raise ValueError("--bits must contain at least one 0 or 1")
    if len(bits) > 65535:
        raise ValueError("--bits is too long")
    return bits


def main() -> None:
    args = parse_args()
    config = ChannelConfig.from_env()
    if not math.isfinite(args.lead_in_ms) or args.lead_in_ms < 0:
        raise ValueError("lead-in must be finite and nonnegative")
    if args.mode not in ("framed", "raw"):
        raise ValueError("mode must be framed or raw")

    if args.mode == "framed":
        payload = args.message.encode("utf-8")
        symbols = frame_symbols(payload, config.calibration_pairs)
        data_bits = bytes_to_bits(len(payload).to_bytes(2, "big") + payload)
        message: str | None = args.message
    else:
        data_bits = _raw_bits(args.bits)
        symbols = raw_symbols(data_bits, config.calibration_pairs)
        payload = b""
        message = None

    wait_for_resource(config.host, config.port, args.startup_timeout)
    if args.lead_in_ms:
        time.sleep(args.lead_in_ms / 1000.0)

    pulses: list[dict[str, object]] = []
    started_at = utc_now_iso()
    first_start: float | None = None
    print(
        f"sender starting: mode={args.mode} symbols={len(symbols)} "
        f"(bit0={config.bit0_ms}ms bit1={config.bit1_ms}ms "
        f"sync={config.sync_ms}ms gap={config.gap_ms}ms)",
        flush=True,
    )

    for index, symbol in enumerate(symbols):
        hold_ms = config.duration_ms(symbol)
        started = time.monotonic()
        if first_start is None:
            first_start = started
        wire_ms = post_with_hold(
            config.host, config.port, b"x" * config.body_bytes, hold_ms, config.timeout_s
        )
        finished = time.monotonic()
        pulses.append(
            {
                "index": index,
                "symbol": symbol,
                "hold_ms": hold_ms,
                "started_ms": round((started - first_start) * 1000.0, 3),
                "finished_ms": round((finished - first_start) * 1000.0, 3),
                "wire_ms": round(wire_ms, 3),
            }
        )
        time.sleep(config.gap_ms / 1000.0)

    finished_at = time.monotonic()
    record = {
        "version": 2,
        "role": "sender",
        "started_at": started_at,
        "mode": args.mode,
        "message": message,
        "payload_hex": payload.hex(),
        "payload_bytes": len(payload),
        "data_bits": data_bits,
        "symbols": symbols,
        "pulses": pulses,
        "config": config.as_dict(),
        "send_span_ms": round((finished_at - (first_start or finished_at)) * 1000.0, 3),
    }
    print("SENDER_RECORD " + json.dumps(record, sort_keys=True), flush=True)
    print(
        f"sender done: {len(pulses)} pulses, {len(data_bits)} data bits, "
        f"{record['send_span_ms']:.0f}ms",
        flush=True,
    )


if __name__ == "__main__":
    main()
