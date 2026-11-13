"""Channel timing parameters, loaded from environment variables."""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    """Timing parameters shared by every role.

    A pulse is one HTTP request whose body is held back for a fixed interval.
    Because the resource handles one request at a time, the receiver's ``/ping``
    queues behind that request and its round-trip time reveals the pulse width.
    """

    host: str = "resource"
    port: int = 8080
    body_bytes: int = 64

    # Pulse widths.  ``sync`` is a long pulse used for framing.
    bit0_ms: float = 80.0
    bit1_ms: float = 240.0
    sync_ms: float = 600.0
    gap_ms: float = 150.0

    # Receiver sampling and decoding thresholds.
    poll_ms: float = 2.0
    bit_threshold_ms: float = 160.0
    sync_threshold_ms: float = 400.0
    # Ignore short RTT blips caused by host/VM scheduling.  Real zero pulses
    # are 80 ms in the baseline, so this leaves a 30 ms margin.
    event_threshold_ms: float = 50.0
    timeout_s: float = 5.0
    # Zero preserves decoding of old captures; new runs use four known 0/1 pairs.
    calibration_pairs: int = 0

    def __post_init__(self) -> None:
        for name in (
            "bit0_ms",
            "bit1_ms",
            "sync_ms",
            "gap_ms",
            "poll_ms",
            "bit_threshold_ms",
            "sync_threshold_ms",
            "event_threshold_ms",
            "timeout_s",
        ):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if not isinstance(self.calibration_pairs, int) or not 0 <= self.calibration_pairs <= 64:
            raise ValueError("calibration_pairs must be an integer in 0..64")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("port must be in 1..65535")
        if self.body_bytes <= 0:
            raise ValueError("body_bytes must be positive")
        if not 0 < self.bit0_ms < self.bit1_ms < self.sync_ms:
            raise ValueError("expected 0 < bit0_ms < bit1_ms < sync_ms")
        if self.gap_ms < 0:
            raise ValueError("gap_ms cannot be negative")
        if self.poll_ms <= 0:
            raise ValueError("poll_ms must be positive")
        if not 0 < self.event_threshold_ms < self.bit0_ms:
            raise ValueError("event_threshold_ms must be positive and below bit0_ms")
        if not self.bit0_ms < self.bit_threshold_ms < self.bit1_ms:
            raise ValueError("bit_threshold_ms must sit between bit0_ms and bit1_ms")
        if not self.bit1_ms < self.sync_threshold_ms < self.sync_ms:
            raise ValueError("sync_threshold_ms must sit between bit1_ms and sync_ms")
        if self.timeout_s <= self.sync_ms / 1000:
            raise ValueError("timeout_s must exceed the sync pulse")

    @property
    def interval_s(self) -> float:
        return self.poll_ms / 1000.0

    def duration_ms(self, symbol: int | None) -> float:
        """Pulse width for a symbol; ``None`` is the framing sync pulse."""
        if symbol is None:
            return self.sync_ms
        if symbol == 0:
            return self.bit0_ms
        if symbol == 1:
            return self.bit1_ms
        raise ValueError(f"invalid symbol: {symbol!r}")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_env(cls) -> ChannelConfig:
        return cls(
            host=os.environ.get("CHANNEL_HOST", "resource"),
            port=_env_int("CHANNEL_PORT", 8080),
            body_bytes=_env_int("CHANNEL_BODY_BYTES", 64),
            bit0_ms=_env_float("CHANNEL_BIT0_MS", 80.0),
            bit1_ms=_env_float("CHANNEL_BIT1_MS", 240.0),
            sync_ms=_env_float("CHANNEL_SYNC_MS", 600.0),
            gap_ms=_env_float("CHANNEL_GAP_MS", 150.0),
            poll_ms=_env_float("CHANNEL_POLL_MS", 2.0),
            bit_threshold_ms=_env_float("CHANNEL_BIT_THRESHOLD_MS", 160.0),
            sync_threshold_ms=_env_float("CHANNEL_SYNC_THRESHOLD_MS", 400.0),
            event_threshold_ms=_env_float("CHANNEL_EVENT_THRESHOLD_MS", 50.0),
            timeout_s=_env_float("CHANNEL_TIMEOUT_S", 5.0),
            calibration_pairs=_env_int("CHANNEL_CALIBRATION_PAIRS", 4),
        )
