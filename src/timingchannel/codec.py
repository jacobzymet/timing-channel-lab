"""Bit packing, framing and sample decoding.

The decoder is deliberately independent of the sender: it only sees the
receiver's list of ``(timestamp, round-trip time)`` samples.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Final

from timingchannel.config import ChannelConfig

SYNC: Final = None
Symbol = int | None


def bytes_to_bits(data: bytes) -> list[int]:
    """Most-significant-bit-first bit list."""
    return [(byte >> shift) & 1 for byte in data for shift in range(7, -1, -1)]


def bits_to_bytes(bits: Sequence[int]) -> bytes:
    if len(bits) % 8:
        raise ValueError("bit count must be a multiple of 8")
    output = bytearray()
    for offset in range(0, len(bits), 8):
        value = 0
        for bit in bits[offset : offset + 8]:
            if bit not in (0, 1):
                raise ValueError(f"invalid bit: {bit!r}")
            value = (value << 1) | bit
        output.append(value)
    return bytes(output)


def raw_symbols(bits: Sequence[int], calibration_pairs: int = 0) -> list[Symbol]:
    """Sync, public alternating calibration pulses, data, sync."""
    if not isinstance(calibration_pairs, int) or not 0 <= calibration_pairs <= 64:
        raise ValueError("calibration_pairs must be an integer in 0..64")
    if any(bit not in (0, 1) for bit in bits):
        raise ValueError("data must contain only bits")
    return [SYNC, *([0, 1] * calibration_pairs), *bits, SYNC]


def frame_symbols(payload: bytes, calibration_pairs: int = 0) -> list[Symbol]:
    """Sync, calibration, 16-bit big-endian length, payload, sync."""
    if len(payload) > 0xFFFF:
        raise ValueError("payload is too large for the 16-bit length field")
    length_bits = bytes_to_bits(len(payload).to_bytes(2, "big"))
    return raw_symbols([*length_bits, *bytes_to_bits(payload)], calibration_pairs)


@dataclass(frozen=True, slots=True)
class Sample:
    t_ms: float
    rtt_ms: float
    timeout: bool = False

    def __post_init__(self) -> None:
        if not all(isfinite(value) and value >= 0 for value in (self.t_ms, self.rtt_ms)):
            raise ValueError("sample timestamp and RTT must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class Event:
    sample_index: int
    t_ms: float
    rtt_ms: float


@dataclass(frozen=True, slots=True)
class FrameRead:
    bits: tuple[int, ...]
    events: tuple[Event, ...]
    preamble: Event | None
    postamble: Event | None
    error: str | None
    calibration_events: tuple[Event, ...] = ()
    data_events: tuple[Event, ...] = ()
    bit_threshold_ms: float | None = None

    @property
    def symbols_observed(self) -> int:
        return len(self.bits)


@dataclass(frozen=True, slots=True)
class FramedDecode:
    payload: bytes | None
    frame: FrameRead
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None and self.payload is not None


def extract_events(samples: Iterable[Sample], config: ChannelConfig) -> list[Event]:
    """A sample is an event when its RTT clears the idle-noise floor."""
    events: list[Event] = []
    previous_time = -1.0
    for index, sample in enumerate(samples):
        if sample.t_ms < previous_time:
            raise ValueError("samples must be in timestamp order")
        previous_time = sample.t_ms
        if sample.timeout:
            continue
        if sample.rtt_ms >= config.event_threshold_ms:
            events.append(Event(index, sample.t_ms, sample.rtt_ms))
    return events


def classify_pulses(
    values: Sequence[float], config: ChannelConfig
) -> tuple[tuple[int, ...], float, str | None]:
    """Calibrate from public training pulses only, then classify unseen data."""
    count = 2 * config.calibration_pairs
    threshold = config.bit_threshold_ms
    if any(not isfinite(value) or value < 0 for value in values):
        return (), threshold, "pulse measurements must be finite and nonnegative"
    if count:
        if len(values) < count:
            return (), threshold, "calibration pulses truncated"
        zero = median(values[:count:2])
        one = median(values[1:count:2])
        if one <= zero:
            return (), threshold, "calibration classes overlap or are reversed"
        threshold = (zero + one) / 2
    return tuple(int(value >= threshold) for value in values[count:]), threshold, None


def read_frame(samples: Iterable[Sample], config: ChannelConfig) -> FrameRead:
    """Find the preamble and collect one bit per following pulse.

    Sampling stops at the postamble.  Missing or extra pulses are reported
    rather than silently repaired so bit-error measurements stay honest.
    """
    events = tuple(extract_events(samples, config))
    preamble_index: int | None = None
    for index, event in enumerate(events):
        if event.rtt_ms >= config.sync_threshold_ms:
            preamble_index = index
            break

    if preamble_index is None:
        return FrameRead((), events, None, None, "preamble not observed")

    preamble = events[preamble_index]
    data: list[Event] = []
    postamble: Event | None = None
    for event in events[preamble_index + 1 :]:
        if event.rtt_ms >= config.sync_threshold_ms:
            postamble = event
            break
        data.append(event)

    bits, threshold, calibration_error = classify_pulses([event.rtt_ms for event in data], config)

    if postamble is None:
        error = "postamble not observed"
    elif calibration_error:
        error = calibration_error
    elif not bits:
        error = "no data pulses between preamble and postamble"
    else:
        error = None
    count = 2 * config.calibration_pairs
    return FrameRead(
        bits,
        events,
        preamble,
        postamble,
        error,
        tuple(data[:count]),
        tuple(data[count:]),
        threshold,
    )


def decode_framed(samples: Iterable[Sample], config: ChannelConfig) -> FramedDecode:
    frame = read_frame(samples, config)
    if frame.error is not None:
        return FramedDecode(None, frame, frame.error)
    if len(frame.bits) < 16:
        return FramedDecode(None, frame, f"length field truncated ({len(frame.bits)} bits)")
    try:
        length = int.from_bytes(bits_to_bytes(frame.bits[:16]), "big")
    except ValueError as exc:  # pragma: no cover - defensive
        return FramedDecode(None, frame, str(exc))

    needed = 16 + 8 * length
    if len(frame.bits) < needed:
        return FramedDecode(
            None,
            frame,
            f"payload truncated (need {needed} bits, got {len(frame.bits)})",
        )
    if len(frame.bits) > needed:
        return FramedDecode(
            None,
            frame,
            f"{len(frame.bits) - needed} unexpected trailing bits",
        )
    try:
        payload = bits_to_bytes(frame.bits[16:needed])
    except ValueError as exc:  # pragma: no cover - defensive
        return FramedDecode(None, frame, str(exc))
    return FramedDecode(payload, frame, None)


def edit_distance(left: Sequence[int], right: Sequence[int]) -> int:
    """Levenshtein distance, used so dropped/extra pulses are penalised."""
    # Exact/near-exact long transfers should not incur quadratic work over the
    # matching region. Removing a common prefix/suffix preserves edit distance.
    start = 0
    left_end, right_end = len(left), len(right)
    while start < min(left_end, right_end) and left[start] == right[start]:
        start += 1
    while left_end > start and right_end > start and left[left_end - 1] == right[right_end - 1]:
        left_end -= 1
        right_end -= 1
    left, right = left[start:left_end], right[start:right_end]
    if not left or not right:
        return max(len(left), len(right))
    if len(right) > len(left):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, left_bit in enumerate(left, start=1):
        current = [i]
        for j, right_bit in enumerate(right, start=1):
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (left_bit != right_bit),
                )
            )
        previous = current
    return previous[-1]


def bit_error_rate(expected: Sequence[int], observed: Sequence[int]) -> float:
    if not expected:
        if observed:
            raise ValueError("cannot normalize errors without expected bits")
        return 0.0
    return edit_distance(expected, observed) / len(expected)


def event_span_ms(frame: FrameRead) -> float | None:
    if frame.preamble is None or frame.postamble is None:
        return None
    return frame.postamble.t_ms - frame.preamble.t_ms
