"""Reproduce the checked-in 100-event reduction using the real subprocess adapter."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from failshrink import Trace, reduce_trace
from failshrink.runner import CommandOracle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("choose a new output directory")
    oracle = CommandOracle([sys.executable, str(ROOT / "examples/ledger_oracle.py"), "{input}"])
    reduced, report = reduce_trace(Trace.load(ROOT / "examples/webhook-trace.json"), oracle)
    summary = {
        key: report[key]
        for key in [
            "status",
            "target_signature",
            "original_count",
            "reduced_count",
            "retained_ids",
            "oracle_calls",
            "cache_hits",
            "fresh_final_replay",
            "minimality_verified",
            "original_sha256",
            "reduced_sha256",
            "minimality_scope",
        ]
    }
    args.out.mkdir(parents=True, exist_ok=False)
    for name, value in [
        ("reduced.json", reduced),
        ("report.json", report),
        ("summary.json", summary),
    ]:
        with (args.out / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
