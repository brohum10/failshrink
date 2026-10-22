"""Generate the intentionally buggy, synthetic webhook-replay demonstration."""

import argparse
import json
from pathlib import Path


def fixture():
    important = {
        7: {"id": "account-open", "op": "open", "account": "demo"},
        35: {
            "id": "payment-first",
            "requires": ["account-open"],
            "op": "credit",
            "account": "demo",
            "payment": "payment-42",
            "amount": 100,
        },
        64: {
            "id": "payment-retry",
            "requires": ["payment-first"],
            "op": "credit",
            "account": "demo",
            "payment": "payment-42",
            "amount": 100,
        },
        91: {
            "id": "balance-audit",
            "requires": ["payment-retry"],
            "op": "audit",
            "account": "demo",
        },
    }
    events = [
        important[i] if i in important else {"id": f"noise-{i:03}", "op": "noop", "tick": i}
        for i in range(100)
    ]
    return {"version": 1, "events": events}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(fixture(), stream, indent=2)
        stream.write("\n")
