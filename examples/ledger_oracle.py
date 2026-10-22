"""Synthetic regression oracle: one implementation forgets webhook deduplication.

Exit zero and emit a protocol result. A crash is not the intended failure.
The fixed implementation applies each payment ID only once.
"""

import argparse
import json


def check(document, *, fixed=False):
    balances, expected, seen = {}, {}, set()
    for event in document["events"]:
        op = event.get("op")
        account = event.get("account")
        if op == "open":
            balances[account] = expected[account] = 0
        elif op == "credit":
            if account not in balances:
                return {"status": "unresolved"}
            key = (account, event["payment"])
            if not fixed or key not in seen:
                balances[account] += event["amount"]
            if key not in seen:
                expected[account] += event["amount"]
                seen.add(key)
        elif op == "audit":
            if account not in balances:
                return {"status": "unresolved"}
            if balances[account] != expected[account]:
                return {"status": "fail", "signature": "duplicate-payment-overcredit"}
    return {"status": "pass"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--fixed", action="store_true")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as source:
        print(json.dumps(check(json.load(source), fixed=args.fixed)))
