from dataclasses import replace

import pytest

from timingchannel.analysis import _log_decode
from timingchannel.codec import (
    Sample,
    bit_error_rate,
    decode_framed,
    frame_symbols,
    raw_symbols,
    read_frame,
)
from timingchannel.config import ChannelConfig


def samples_for(symbols, config, offset=1.5):
    return [
        Sample(index * 1000, config.duration_ms(symbol) + offset)
        for index, symbol in enumerate(symbols)
    ]


def test_calibration_corrects_offset_without_payload_labels():
    config = ChannelConfig(bit0_ms=120, bit1_ms=122, bit_threshold_ms=121, calibration_pairs=4)
    samples = samples_for(frame_symbols(b"unseen payload", 4), config)
    decoded = decode_framed(samples, config)
    assert decoded.payload == b"unseen payload"
    assert decoded.frame.bit_threshold_ms == 122.5
    assert len(decoded.frame.calibration_events) == 8
    assert len(decoded.frame.bits) == 16 + 8 * len(b"unseen payload")


def test_calibration_median_tolerates_one_training_outlier():
    config = ChannelConfig(calibration_pairs=4)
    samples = samples_for(raw_symbols([0, 1, 0], 4), config)
    samples[1] = replace(samples[1], rtt_ms=250)
    assert read_frame(samples, config).bits == (0, 1, 0)


@pytest.mark.parametrize("durations", [[600, 80, 600], [600, 240, 80, 600]])
def test_bad_calibration_is_reported(durations):
    config = ChannelConfig(calibration_pairs=1)
    frame = read_frame([Sample(i * 1000, rtt) for i, rtt in enumerate(durations)], config)
    assert frame.error and "calibration" in frame.error
    assert not frame.bits


def test_legacy_capture_keeps_fixed_threshold():
    config = ChannelConfig()
    frame = read_frame(samples_for(raw_symbols([0, 1]), config), config)
    assert frame.bits == (0, 1)
    assert frame.bit_threshold_ms == 160


def test_log_calibration_does_not_use_expected_payload():
    config = ChannelConfig(bit0_ms=120, bit1_ms=122, bit_threshold_ms=121, calibration_pairs=4)
    bits = [0, 1, 0, 0, 1]
    timestamp = 0.0
    events = []
    for symbol in raw_symbols(bits, 4):
        duration = config.duration_ms(symbol) + 1.5
        events.append(
            {
                "t_start_ms": timestamp,
                "read_ms": duration,
                "sha256": "constant",
                "path": "/work",
                "bytes": 64,
            }
        )
        timestamp += duration + config.gap_ms
    correct = _log_decode(events, config, bits)
    wrong_labels = _log_decode(events, config, [1 - bit for bit in bits])
    assert correct["bits"] == correct["read_ms_bits"] == bits
    assert wrong_labels["bits"] == wrong_labels["read_ms_bits"] == bits
    assert correct["ber"] == correct["read_ms_ber"] == 0
    assert wrong_labels["ber"] > 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("poll_ms", float("nan")),
        ("gap_ms", float("inf")),
        ("timeout_s", float("nan")),
        ("event_threshold_ms", -1),
        ("calibration_pairs", -1),
        ("calibration_pairs", 1.5),
    ],
)
def test_invalid_configuration_is_rejected(field, value):
    with pytest.raises(ValueError):
        ChannelConfig(**{field: value})


def test_empty_expected_bits_cannot_hide_insertions():
    assert bit_error_rate([], []) == 0
    with pytest.raises(ValueError):
        bit_error_rate([], [1])
