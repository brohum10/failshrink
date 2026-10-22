"""FailShrink command-line entry point."""

import argparse
from pathlib import Path
import sys

from .core import NotReproducible, Trace, canonical, reduce_trace
from .runner import CommandOracle


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Reduce an event trace while preserving a failure signature."
    )
    parser.add_argument("trace", type=Path)
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="new directory; existing paths are never overwritten",
    )
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-calls", type=int, default=400)
    parser.add_argument("--timeout", type=float, default=3)
    parser.add_argument(
        "--oracle",
        nargs=argparse.REMAINDER,
        required=True,
        help="trusted command and args, with one {input} placeholder; put last",
    )
    args = parser.parse_args(argv)
    try:
        if args.out.exists() or args.out.is_symlink():
            raise ValueError("output path already exists; choose a new directory")
        trace = Trace.load(args.trace)
        command = CommandOracle(args.oracle, timeout=args.timeout)
        reduced, report = reduce_trace(
            trace, command, repeats=args.repeats, max_calls=args.max_calls
        )
        args.out.mkdir(parents=True, exist_ok=False)
        for name, value in (("reduced.json", reduced), ("report.json", report)):
            with (args.out / name).open("x", encoding="utf-8") as target:
                target.write(canonical(value) + "\n")
        print(
            f'{report["status"]}: {report["original_count"]} -> {report["reduced_count"]} events; '
            f'{report["oracle_calls"]} oracle calls; minimality verified: {report["minimality_verified"]}'
        )
        return {"complete": 0, "budget_exhausted": 4, "incomplete": 5, "unstable_final": 5}[
            report["status"]
        ]
    except NotReproducible as exc:
        print(f"not reproducible: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, RecursionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
