"""Rebuild the aggregate table from every analysis.json under results/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_experiment import _aggregate, _format_summary, completed_analyses  # noqa: E402

from timingchannel.analysis import analyze_run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate completed timing-channel runs")
    parser.add_argument("--results", type=Path, default=ROOT / "results" / "calibrated")
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Re-run the host decoder on every complete run before aggregating",
    )
    args = parser.parse_args()

    if args.reanalyze:
        for path in sorted(args.results.glob("*/receiver.json")):
            run_dir = path.parent
            if (run_dir / "sender.json").exists() and not (run_dir / "error.txt").exists():
                data = analyze_run(run_dir)
                (run_dir / "summary.md").write_text(_format_summary(data), encoding="utf-8")
    analyses = completed_analyses(args.results)
    if not analyses:
        raise SystemExit(f"no analysis.json files under {args.results}")

    analyses.sort(key=lambda item: (str(item.get("scenario")), str(item.get("run"))))
    (args.results / "RESULTS.md").write_text(_aggregate([], analyses), encoding="utf-8")
    (args.results / "aggregate.json").write_text(
        json.dumps(analyses, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"aggregated {len(analyses)} runs into {args.results / 'RESULTS.md'}")


if __name__ == "__main__":
    main()
