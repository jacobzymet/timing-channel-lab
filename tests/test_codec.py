from __future__ import annotations

from timingchannel.analysis import _log_decode
from timingchannel.codec import (
    Sample,
    bit_error_rate,
    bits_to_bytes,
    bytes_to_bits,
    decode_framed,
    frame_symbols,
)
from timingchannel.config import ChannelConfig


def test_bit_packing_roundtrip() -> None:
    data = b"timing\x00\xff"
    assert bits_to_bytes(bytes_to_bits(data)) == data
    assert bytes_to_bits(b"\x80") == [1, 0, 0, 0, 0, 0, 0, 0]


def test_frame_has_preamble_length_and_postamble() -> None:
    symbols = frame_symbols(b"HI")
    assert symbols[0] is None
    assert symbols[-1] is None
    assert symbols[1:17] == bytes_to_bits((2).to_bytes(2, "big"))
    assert len(symbols) == 2 + 16 + 16


def test_decode_framed_from_synthetic_samples() -> None:
    config = ChannelConfig()
    payload = b"HI"
    samples: list[Sample] = []
    timestamp = 0.0
    for symbol in frame_symbols(payload):
        samples.append(Sample(t_ms=timestamp, rtt_ms=config.duration_ms(symbol) - 1.0))
        timestamp += config.duration_ms(symbol) + config.gap_ms
    samples.append(Sample(t_ms=timestamp, rtt_ms=0.5))

    decoded = decode_framed(samples, config)
    assert decoded.error is None, decoded.error
    assert decoded.payload == payload
    assert decoded.ok


def test_decode_rejects_trailing_bits() -> None:
    config = ChannelConfig()
    samples: list[Sample] = []
    timestamp = 0.0
    symbols = frame_symbols(b"A")
    symbols = [*symbols[:-1], 1, None]
    for symbol in symbols:
        samples.append(Sample(t_ms=timestamp, rtt_ms=config.duration_ms(symbol) - 1.0))
        timestamp += config.duration_ms(symbol) + config.gap_ms
    decoded = decode_framed(samples, config)
    assert not decoded.ok
    assert "trailing bits" in (decoded.error or "")


def test_bit_error_rate_penalises_drops_and_insertions() -> None:
    assert bit_error_rate([0, 1, 1, 0], [0, 1, 1, 0]) == 0.0
    assert bit_error_rate([0, 1, 1, 0], [0, 0, 1, 0]) == 0.25
    assert bit_error_rate([0, 1, 1, 0], [0, 1, 0]) == 0.25
    assert bit_error_rate([0, 1, 1, 0], [0, 1, 1, 0, 0]) == 0.25


def test_log_decode_recovers_bits_from_request_starts() -> None:
    config = ChannelConfig()
    bits = [0, 1, 1, 0, 1, 0, 0, 1]
    starts = [0.0]
    starts.append(starts[-1] + config.sync_ms + config.gap_ms)
    for bit in bits:
        starts.append(starts[-1] + config.duration_ms(bit) + config.gap_ms)
    durations = [config.sync_ms, *[config.duration_ms(bit) for bit in bits], config.sync_ms]
    events = [
        {
            "event": "work",
            "path": "/work",
            "bytes": 64,
            "sha256": "same",
            "t_start_ms": start,
            "read_ms": duration,
        }
        for start, duration in zip(starts, durations, strict=True)
    ]
    result = _log_decode(events, config, bits)
    assert result["error"] is None
    assert result["bits"] == bits
    assert result["ber"] == 0.0
    assert result["read_ms_bits"] == bits
    assert result["read_ms_ber"] == 0.0
    assert result["unique_body_sha256"] == 1
