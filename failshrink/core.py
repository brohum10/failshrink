"""Complement-based delta debugging with explicit dependency-pruned minimality."""

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable

MAX_BYTES = 5_000_000
MAX_EVENTS = 10_000
ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


@dataclass(frozen=True)
class Event:
    id: str
    requires: tuple[str, ...]
    encoded: str


@dataclass(frozen=True)
class Trace:
    events: tuple[Event, ...]

    @classmethod
    def from_document(cls, document):
        if type(document) is not dict or set(document) != {"version", "events"}:
            raise ValueError("trace must contain exactly version and events")
        if type(document["version"]) is not int or document["version"] != 1:
            raise ValueError("version must be integer 1")
        rows = document["events"]
        if type(rows) is not list or len(rows) > MAX_EVENTS:
            raise ValueError(f"events must be a list of at most {MAX_EVENTS} items")
        seen, events = set(), []
        for row in rows:
            if (
                type(row) is not dict
                or type(row.get("id")) is not str
                or not ID.fullmatch(row["id"])
            ):
                raise ValueError("each event needs a printable ASCII id (letters, digits, _ . : -)")
            key = row["id"]
            if key in seen:
                raise ValueError(f"duplicate event id: {key}")
            refs = row.get("requires", [])
            if type(refs) is not list or any(type(r) is not str for r in refs):
                raise ValueError(f"requires must be a list of ids: {key}")
            if len(set(refs)) != len(refs) or not set(refs) <= seen:
                raise ValueError(f"dependencies must be unique earlier event ids: {key}")
            events.append(Event(key, tuple(refs), canonical(row)))
            seen.add(key)
        result = cls(tuple(events))
        if len(canonical(result.document()).encode()) > MAX_BYTES:
            raise ValueError(f"canonical trace exceeds {MAX_BYTES} bytes")
        return result

    @classmethod
    def load(cls, path):
        with open(path, "rb") as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError(f"trace exceeds {MAX_BYTES} bytes")
        return cls.from_document(strict_json(raw.decode("utf-8")))

    def document(self, indices=None):
        if indices is None:
            indices = tuple(range(len(self.events)))
        return {"version": 1, "events": [json.loads(self.events[i].encoded) for i in indices]}

    def prune(self, retained):
        """Remove dangling dependents iteratively, keeping original event order."""
        retained = set(retained)
        if any(type(i) is not int or not 0 <= i < len(self.events) for i in retained):
            raise ValueError("invalid event index")
        kept, available = [], set()
        for i, event in enumerate(self.events):
            if i in retained and all(ref in available for ref in event.requires):
                kept.append(i)
                available.add(event.id)
        return tuple(kept)


@dataclass(frozen=True)
class Verdict:
    status: str
    signature: str = ""
    reason: str = ""

    def __post_init__(self):
        if any(type(value) is not str for value in (self.status, self.signature, self.reason)):
            raise ValueError("oracle verdict fields must be strings")
        if self.status not in {"pass", "fail", "unresolved"}:
            raise ValueError("invalid oracle status")
        if self.status == "fail" and (not self.signature or len(self.signature) > 200):
            raise ValueError("fail needs a nonempty signature of at most 200 characters")
        if self.status != "fail" and self.signature:
            raise ValueError("only fail may have a signature")


class BudgetExceeded(Exception):
    pass


class NotReproducible(ValueError):
    pass


class Evaluator:
    def __init__(self, trace, oracle, repeats, max_calls):
        self.trace, self.oracle = trace, oracle
        self.repeats, self.max_calls = repeats, max_calls
        self.calls, self.hits = 0, 0
        self.cache, self.audit = {}, []

    def evaluate(self, indices, *, fresh=False, reserve=0):
        if not fresh and indices in self.cache:
            self.hits += 1
            return self.cache[indices]
        if self.calls + self.repeats + reserve > self.max_calls:
            raise BudgetExceeded
        outcomes = []
        for _ in range(self.repeats):
            self.calls += 1
            try:
                # A fresh document prevents predicates from mutating cached source data.
                answer = self.oracle(self.trace.document(indices))
                if not isinstance(answer, Verdict):
                    answer = Verdict("unresolved", reason="invalid_return_type")
            except Exception as exc:
                answer = Verdict("unresolved", reason="oracle_exception:" + type(exc).__name__)
            outcomes.append(answer)
        keys = {(v.status, v.signature, v.reason) for v in outcomes}
        result = (
            outcomes[0] if len(keys) == 1 else Verdict("unresolved", reason="inconsistent_repeats")
        )
        self.cache[indices] = result
        self.audit.append(
            {
                "ids": [self.trace.events[i].id for i in indices],
                "trace_sha256": digest(self.trace.document(indices)),
                "fresh": fresh,
                "observations": [
                    {"status": v.status, "signature": v.signature, "reason": v.reason}
                    for v in outcomes
                ],
                "result": {
                    "status": result.status,
                    "signature": result.signature,
                    "reason": result.reason,
                },
            }
        )
        return result


def reduce_trace(trace: Trace, oracle: Callable, *, repeats=2, max_calls=400):
    """Keep the same stable failure, then audit each dependency-pruned deletion.

    This is not a global minimum search. An unresolved neighbor or exhausted
    budget prevents a verified-minimality claim. Final replay bypasses the cache.
    """
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError("repeats must be an integer from 1 to 20")
    if type(max_calls) is not int or max_calls < 2 * repeats:
        raise ValueError("max_calls must allow baseline and fresh final replay")
    evaluator = Evaluator(trace, oracle, repeats, max_calls)
    current = tuple(range(len(trace.events)))
    original = evaluator.evaluate(current)
    if original.status != "fail":
        raise NotReproducible("original trace did not reproduce one stable failure signature")
    signature = original.signature

    def matches(answer):
        return answer.status == "fail" and answer.signature == signature

    def attempt(indices):
        return evaluator.evaluate(indices, reserve=repeats)

    exhausted, witnesses = False, []
    try:
        # Empty is a meaningful candidate too; do not assume at least one event is needed.
        if current and matches(attempt(())):
            current = ()
        granularity = 2
        while len(current) >= 2:
            width = (len(current) + granularity - 1) // granularity
            accepted = False
            for start in range(0, len(current), width):
                candidate = trace.prune(current[:start] + current[start + width :])
                if matches(attempt(candidate)):
                    current = candidate
                    granularity = max(2, granularity - 1)
                    accepted = True
                    break
            if not accepted:
                if granularity >= len(current):
                    break
                granularity = min(len(current), granularity * 2)
        # A separate complete pass verifies the precise local-minimality contract.
        while True:
            witnesses, accepted = [], False
            for removed in current:
                candidate = trace.prune(i for i in current if i != removed)
                verdict = attempt(candidate)
                witnesses.append(
                    {
                        "removed_id": trace.events[removed].id,
                        "remaining_count": len(candidate),
                        "trace_sha256": digest(trace.document(candidate)),
                        "status": verdict.status,
                        "signature": verdict.signature,
                        "reason": verdict.reason,
                    }
                )
                if matches(verdict):
                    current, accepted = candidate, True
                    break
            if not accepted:
                break
    except BudgetExceeded:
        exhausted = True
    final = evaluator.evaluate(current, fresh=True)
    replayed = matches(final)
    verified = (
        not exhausted
        and replayed
        and len(witnesses) == len(current)
        and all(w["status"] != "unresolved" for w in witnesses)
    )
    status = (
        "unstable_final"
        if not replayed
        else "budget_exhausted" if exhausted else "complete" if verified else "incomplete"
    )
    retained_set = set(current)
    report = {
        "version": 1,
        "status": status,
        "target_signature": signature,
        "original_count": len(trace.events),
        "reduced_count": len(current),
        "original_sha256": digest(trace.document()),
        "reduced_sha256": digest(trace.document(current)),
        "retained_ids": [trace.events[i].id for i in current],
        "removed_ids": [e.id for i, e in enumerate(trace.events) if i not in retained_set],
        "fresh_final_replay": replayed,
        "minimality_verified": verified,
        "minimality_scope": "single-event deletion plus transitive dependent pruning; not global minimum",
        "repeats": repeats,
        "max_oracle_calls": max_calls,
        "oracle_calls": evaluator.calls,
        "cache_hits": evaluator.hits,
        "minimality_witnesses": witnesses,
        "evaluations": evaluator.audit,
    }
    return trace.document(current), report
