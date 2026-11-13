"""Plot one receiver trace as an SVG or PNG (optional matplotlib dependency)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from timingchannel.codec import Sample, read_frame  # noqa: E402
from timingchannel.config import ChannelConfig  # noqa: E402
from timingchannel.wire import read_json  # noqa: E402

PLOT_WIDTH = 12
PLOT_HEIGHT = 4.5


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot a receiver timing trace")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - optional tooling
        raise SystemExit("install the plot extra: pip install -e '.[plot]'") from exc

    receiver = read_json(args.run_dir / "receiver.json")
    sender = read_json(args.run_dir / "sender.json")
    config = ChannelConfig(**receiver["config"])
    samples = [Sample(**item) for item in receiver["samples"]]
    frame = read_frame(samples, config)
    data = [sample for sample in samples if not sample.timeout]

    figure, axis = plt.subplots(figsize=(PLOT_WIDTH, PLOT_HEIGHT))
    axis.plot(
        [sample.t_ms / 1000.0 for sample in data],
        [sample.rtt_ms for sample in data],
        color="#24557a",
        linewidth=0.8,
        label="receiver /ping RTT",
    )
    axis.axhline(
        frame.bit_threshold_ms or config.bit_threshold_ms,
        color="#b04a42",
        linestyle="--",
        linewidth=1.0,
        label=f"bit threshold ({frame.bit_threshold_ms or config.bit_threshold_ms:.2f} ms)",
    )
    axis.axhline(
        config.sync_threshold_ms,
        color="#7a6a3a",
        linestyle=":",
        linewidth=1.0,
        label=f"sync threshold ({config.sync_threshold_ms:.0f} ms)",
    )

    if frame.preamble is not None:
        axis.axvline(
            frame.preamble.t_ms / 1000.0,
            color="#7a6a3a",
            linewidth=0.8,
            alpha=0.7,
        )
    if frame.postamble is not None:
        axis.axvline(
            frame.postamble.t_ms / 1000.0,
            color="#7a6a3a",
            linewidth=0.8,
            alpha=0.7,
        )

    expected = [int(bit) for bit in sender["data_bits"]]
    if len(frame.bits) == len(expected):
        events = frame.data_events
        for bit, color in ((0, "#3b7d4f"), (1, "#b04a42")):
            xs = [
                event.t_ms / 1000.0
                for event, expected_bit in zip(events, expected, strict=False)
                if expected_bit == bit
            ]
            ys = [
                event.rtt_ms
                for event, expected_bit in zip(events, expected, strict=False)
                if expected_bit == bit
            ]
            axis.scatter(xs, ys, s=16, color=color, zorder=3, label=f"expected {bit}")

    axis.set_xlabel("receiver time (s)")
    axis.set_ylabel("RTT (ms)")
    axis.set_title(f"Timing channel trace: {args.run_dir.name}")
    axis.set_ylim(bottom=0)
    axis.grid(alpha=0.2)
    axis.legend(loc="upper right", fontsize=8, ncols=2)

    output = args.output or (args.run_dir / "timing.png")
    figure.tight_layout()
    figure.savefig(output, dpi=150)
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
