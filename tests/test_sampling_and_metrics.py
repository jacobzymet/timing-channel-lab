import math
import sys

import pytest

import timingchannel.receiver as receiver
from timingchannel.analysis import _log_decode
from timingchannel.codec import Sample, classify_pulses, edit_distance, read_frame
from timingchannel.config import ChannelConfig


class VirtualStop:
    def __init__(self, stop_on_wait=False):
        self.now = 0.0
        self.stopped = False
        self.stop_on_wait = stop_on_wait

    def is_set(self):
        return self.stopped

    def wait(self, duration):
        self.now += duration
        self.stopped = self.stop_on_wait
        return self.stopped


def test_slow_polling_obeys_full_interval_and_capture_deadline(monkeypatch):
    stop = VirtualStop()
    monkeypatch.setattr(receiver.time, "monotonic", lambda: stop.now)
    monkeypatch.setattr(receiver, "ping_once", lambda *args: (0.1, False))
    samples = receiver.sample_latency(ChannelConfig(poll_ms=1000), 2.2, stop, 0)
    assert [sample.t_ms for sample in samples] == [0, 1000, 2000]
    assert stop.now == 2.2


def test_stop_during_long_wait_does_not_send_an_extra_probe(monkeypatch):
    stop = VirtualStop(stop_on_wait=True)
    monkeypatch.setattr(receiver.time, "monotonic", lambda: stop.now)
    monkeypatch.setattr(receiver, "ping_once", lambda *args: (0.1, False))
    assert len(receiver.sample_latency(ChannelConfig(poll_ms=1000), 0, stop, 0)) == 1


def test_sampling_failure_removes_health_marker(monkeypatch, tmp_path):
    marker = tmp_path / "ready"
    monkeypatch.setenv("CHANNEL_READY_FILE", str(marker))
    monkeypatch.delenv("CHANNEL_MESSAGE", raising=False)
    monkeypatch.delenv("CHANNEL_BITS", raising=False)
    monkeypatch.setattr(sys, "argv", ["receiver"])
    monkeypatch.setattr(receiver, "wait_for_resource", lambda *a: None)
    monkeypatch.setattr(receiver.signal, "signal", lambda *a: None)

    def fail(*args):
        assert marker.exists()
        raise RuntimeError("probe failed")

    monkeypatch.setattr(receiver, "sample_latency", fail)
    with pytest.raises(RuntimeError, match="probe failed"):
        receiver.main()
    assert not marker.exists()


@pytest.mark.parametrize("timestamp,rtt", [(math.nan, 1), (0, math.inf), (-1, 1), (0, -1)])
def test_invalid_measurements_fail_instead_of_becoming_symbols(timestamp, rtt):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        Sample(timestamp, rtt)
    assert classify_pulses([math.nan, 240], ChannelConfig(calibration_pairs=1))[2]


def test_out_of_order_samples_are_rejected():
    with pytest.raises(ValueError, match="timestamp order"):
        read_frame([Sample(2, 600), Sample(1, 80)], ChannelConfig())


def work_log(preamble=600, postamble=600):
    starts = [0, preamble + 150, preamble + 380, preamble + 770]
    return [
        {"t_start_ms": t, "read_ms": width, "sha256": "a" * 64, "bytes": 64, "path": "/work"}
        for t, width in zip(starts, [preamble, 80, 240, postamble], strict=True)
    ]


def test_log_without_preamble_cannot_report_perfect_recovery():
    result = _log_decode(work_log(preamble=80), ChannelConfig(), [0, 1])
    assert result["ber"] is None and result["read_ms_ber"] is None
    assert "preamble" in result["error"]


def test_read_log_requires_postamble_timestamp_log_exposes_its_assumption():
    result = _log_decode(work_log(postamble=80), ChannelConfig(), [0, 1])
    assert result["ber"] == 0  # Last width cannot be inferred from starts alone.
    assert result["read_ms_ber"] is None
    assert "postamble" in result["read_ms_error"]


def test_log_decoding_does_not_depend_on_ground_truth_length():
    result = _log_decode(work_log(), ChannelConfig(), [1])
    assert result["bits"] == result["read_ms_bits"] == [0, 1]
    assert result["ber"] is None  # Mismatched count invalidates the score only.


def test_missing_body_hash_is_not_evidence_of_constant_content():
    events = work_log()
    del events[0]["sha256"]
    assert not _log_decode(events, ChannelConfig(), [0, 1])["body_hashes_complete"]


def test_large_nearly_exact_transfer_does_not_require_quadratic_work():
    expected = [0] * 100_000
    assert edit_distance(expected, [*expected[:-1], 1]) == 1
    assert edit_distance(expected, [0, *expected]) == 1
    assert edit_distance(expected, expected[:-1]) == 1


@pytest.mark.parametrize(
    "left,right,distance",
    [
        ([0, 1], [1, 0], 2),
        ([0, 1, 0], [0, 0], 1),
        ([0, 0, 1, 1], [1, 1, 0, 0], 4),
        ([], [1, 0], 2),
    ],
)
def test_edit_distance_keeps_alignment_costs(left, right, distance):
    assert edit_distance(left, right) == distance
    assert edit_distance([1, *left, 0], [1, *right, 0]) == distance
