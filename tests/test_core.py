import itertools
import json
import random
import tempfile
from pathlib import Path
import unittest

from examples.make_trace import fixture
from examples.ledger_oracle import check
from failshrink import Trace, Verdict, reduce_trace
from failshrink.core import NotReproducible, digest, strict_json


def predicate(document):
    result = check(document)
    return Verdict(result["status"], result.get("signature", ""))


class TraceTests(unittest.TestCase):
    def test_schema_rejects_ambiguous_or_invalid_structure(self):
        bad = [
            None,
            [],
            {},
            {"version": True, "events": []},
            {"version": 2, "events": []},
            {"version": 1, "events": [], "other": 0},
            {"version": 1, "events": {}},
            {"version": 1, "events": [None]},
            {"version": 1, "events": [{"id": "bad id"}]},
            {"version": 1, "events": [{"id": "a"}, {"id": "a"}]},
            {"version": 1, "events": [{"id": "a", "requires": "a"}]},
            {"version": 1, "events": [{"id": "a", "requires": [[]]}]},
            {"version": 1, "events": [{"id": "a", "requires": ["a"]}]},
            {"version": 1, "events": [{"id": "a", "requires": ["b"]}, {"id": "b"}]},
            {"version": 1, "events": [{"id": "a"}, {"id": "b", "requires": ["a", "a"]}]},
            {"version": 1, "events": [{"id": "a", "value": float("nan")}]},
            {"version": 1, "events": [{"id": "a"} for _ in range(10001)]},
        ]
        for document in bad:
            with self.subTest(document=str(document)[:100]), self.assertRaises(ValueError):
                Trace.from_document(document)

    def test_strict_json_rejects_duplicate_keys_and_nonfinite(self):
        for text in ['{"version":1,"version":2}', '{"value":NaN}', '{"value":Infinity}']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                strict_json(text)

    def test_oversize_and_invalid_utf8_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            path.write_bytes(b" " * 5_000_001)
            with self.assertRaises(ValueError):
                Trace.load(path)
            path.write_bytes(b"\xff")
            with self.assertRaises(UnicodeError):
                Trace.load(path)

    def test_source_is_detached_from_caller_and_predicate_mutation(self):
        original = {"version": 1, "events": [{"id": "a", "nested": {"value": 4}}]}
        trace = Trace.from_document(original)
        original["events"][0]["nested"]["value"] = 9
        one = trace.document()
        one["events"].clear()
        self.assertEqual(trace.document()["events"][0]["nested"]["value"], 4)

    def test_pruning_deep_chain_is_iterative(self):
        events = [{"id": f"e{i}", "requires": [f"e{i-1}"] if i else []} for i in range(2500)]
        trace = Trace.from_document({"version": 1, "events": events})
        self.assertEqual(trace.prune(range(1, 2500)), ())

    def test_pruning_diamond_preserves_independent_events_and_order(self):
        trace = Trace.from_document(
            {
                "version": 1,
                "events": [
                    {"id": "a"},
                    {"id": "b", "requires": ["a"]},
                    {"id": "c", "requires": ["a"]},
                    {"id": "d", "requires": ["b", "c"]},
                    {"id": "e"},
                ],
            }
        )
        self.assertEqual(trace.prune([4, 3, 2, 0]), (0, 2, 4))
        for indices in [[-1], [6], [True]]:
            with self.assertRaises(ValueError):
                trace.prune(indices)

    def test_pruning_matches_independent_dependency_closure_200_graphs(self):
        rng = random.Random(714)
        for _ in range(200):
            rows = [
                {"id": str(i), "requires": [str(j) for j in range(i) if rng.random() < 0.18]}
                for i in range(rng.randrange(1, 12))
            ]
            trace = Trace.from_document({"version": 1, "events": rows})
            selected = {i for i in range(len(rows)) if rng.random() < 0.6}
            remaining = selected.copy()
            changed = True
            while changed:
                previous = remaining.copy()
                remaining = {
                    i for i in remaining if all(int(r) in remaining for r in rows[i]["requires"])
                }
                changed = previous != remaining
            self.assertEqual(trace.prune(selected), tuple(sorted(remaining)))


class ReducerTests(unittest.TestCase):
    def test_reduces_synthetic_webhooks_and_preserves_payload(self):
        source = fixture()
        reduced, report = reduce_trace(Trace.from_document(source), predicate)
        self.assertEqual(
            [r["id"] for r in reduced["events"]],
            ["account-open", "payment-first", "payment-retry", "balance-audit"],
        )
        self.assertTrue(report["minimality_verified"])
        self.assertTrue(report["fresh_final_replay"])
        self.assertTrue(report["evaluations"][-1]["fresh"])
        self.assertEqual(report["reduced_sha256"], digest(reduced))
        self.assertEqual(reduced["events"], [r for r in source["events"] if r["op"] != "noop"])

    def test_example_is_also_exhaustively_minimal_within_its_four_events(self):
        trace = Trace.from_document(fixture())
        reduced, _ = reduce_trace(trace, predicate)
        small = Trace.from_document(reduced)
        for count in range(4):
            for subset in itertools.combinations(range(4), count):
                self.assertNotEqual(predicate(small.document(small.prune(subset))).status, "fail")

    def test_empty_failure_is_not_forced_nonempty(self):
        for document in [{"version": 1, "events": []}, {"version": 1, "events": [{"id": "a"}]}]:
            reduced, report = reduce_trace(
                Trace.from_document(document), lambda _: Verdict("fail", "always")
            )
            self.assertEqual(reduced["events"], [])
            self.assertTrue(report["minimality_verified"])

    def test_original_pass_and_unresolved_are_not_reproducible(self):
        trace = Trace.from_document(fixture())
        for answer in [Verdict("pass"), Verdict("unresolved")]:
            with self.assertRaises(NotReproducible):
                reduce_trace(trace, lambda _: answer)

    def test_baseline_flakiness_and_signature_changes_are_rejected(self):
        trace = Trace.from_document(fixture())
        for answers in [
            (Verdict("pass"), Verdict("fail", "a")),
            (Verdict("fail", "a"), Verdict("fail", "b")),
        ]:
            iterator = iter(answers)
            with self.assertRaises(NotReproducible):
                reduce_trace(trace, lambda _: next(iterator))

    def test_other_failure_signature_cannot_replace_target(self):
        trace = Trace.from_document({"version": 1, "events": [{"id": "target"}, {"id": "other"}]})

        def oracle(document):
            ids = {r["id"] for r in document["events"]}
            return (
                Verdict("fail", "target" if "target" in ids else "other")
                if ids
                else Verdict("pass")
            )

        reduced, report = reduce_trace(trace, oracle)
        self.assertEqual([r["id"] for r in reduced["events"]], ["target"])
        self.assertEqual(report["target_signature"], "target")

    def test_unresolved_deletions_do_not_claim_minimality(self):
        trace = Trace.from_document({"version": 1, "events": [{"id": "a"}]})
        _, report = reduce_trace(
            trace, lambda d: Verdict("fail", "x") if d["events"] else Verdict("unresolved")
        )
        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(report["minimality_verified"])

    def test_budget_reserves_fresh_final_replay_and_reports_partial(self):
        trace = Trace.from_document(fixture())
        _, report = reduce_trace(trace, predicate, repeats=2, max_calls=4)
        self.assertEqual(report["status"], "budget_exhausted")
        self.assertEqual(report["oracle_calls"], 4)
        self.assertTrue(report["fresh_final_replay"])
        self.assertFalse(report["minimality_verified"])

    def test_final_replay_bypasses_cached_failure(self):
        trace = Trace.from_document({"version": 1, "events": []})
        counter = 0

        def oracle(_):
            nonlocal counter
            counter += 1
            return Verdict("fail", "x") if counter <= 2 else Verdict("pass")

        _, report = reduce_trace(trace, oracle)
        self.assertEqual(report["status"], "unstable_final")
        self.assertFalse(report["minimality_verified"])

    def test_invalid_options_and_predicate_errors(self):
        trace = Trace.from_document(fixture())
        for options in [
            {"repeats": True},
            {"repeats": 0},
            {"repeats": 21},
            {"max_calls": True},
            {"max_calls": 3},
        ]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                reduce_trace(trace, predicate, **options)
        for bad in [lambda _: None, lambda _: 1 / 0]:
            with self.assertRaises(NotReproducible):
                reduce_trace(trace, bad)

    def test_cached_search_is_deterministic(self):
        trace = Trace.from_document(fixture())
        a = reduce_trace(trace, predicate)
        b = reduce_trace(trace, predicate)
        self.assertEqual(a, b)
        self.assertGreater(a[1]["cache_hits"], 0)

    def test_verdict_field_types_and_signatures(self):
        for args in [("fail", []), ([], "x"), ("fail", ""), ("fail", "x" * 201), ("pass", "x")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                Verdict(*args)

    def test_candidate_flakiness_is_not_accepted_as_a_reduction(self):
        trace = Trace.from_document({"version": 1, "events": [{"id": "a"}, {"id": "b"}]})
        calls = {}

        def oracle(document):
            count = len(document["events"])
            calls[count] = calls.get(count, 0) + 1
            if count == 2:
                return Verdict("fail", "x")
            if count == 0:
                return Verdict("pass")
            return Verdict("fail", "x") if calls[count] % 2 else Verdict("pass")

        reduced, report = reduce_trace(trace, oracle)
        self.assertEqual(len(reduced["events"]), 2)
        self.assertEqual(report["status"], "incomplete")

    def test_200_nonmonotonic_failure_tables_against_exhaustive_neighbors(self):
        rng = random.Random(1123)
        for _ in range(200):
            size = rng.randint(2, 7)
            rows = [
                {"id": str(i), "requires": [str(j) for j in range(i) if rng.random() < 0.15]}
                for i in range(size)
            ]
            failing = {mask for mask in range(1 << size) if rng.random() < 0.35}
            failing.add((1 << size) - 1)
            trace = Trace.from_document({"version": 1, "events": rows})

            def oracle(document):
                mask = sum(1 << int(event["id"]) for event in document["events"])
                return Verdict("fail", "table-failure") if mask in failing else Verdict("pass")

            reduced, report = reduce_trace(trace, oracle, max_calls=1000)
            self.assertTrue(report["minimality_verified"])
            self.assertEqual(oracle(reduced).status, "fail")
            present = {int(event["id"]) for event in reduced["events"]}
            for removed in present:
                neighbor = present - {removed}
                for _ in range(size):
                    neighbor = {
                        i for i in neighbor if set(map(int, rows[i]["requires"])) <= neighbor
                    }
                self.assertNotIn(sum(1 << i for i in neighbor), failing)

    def test_local_minimality_against_200_independent_small_oracles(self):
        rng = random.Random(919)
        for _ in range(200):
            size = rng.randint(1, 8)
            rows = [
                {"id": str(i), "requires": [str(j) for j in range(i) if rng.random() < 0.1]}
                for i in range(size)
            ]
            required = set(rng.sample(range(size), rng.randint(0, size)))
            trace = Trace.from_document({"version": 1, "events": rows})

            def oracle(document):
                present = {int(r["id"]) for r in document["events"]}
                return Verdict("fail", "x") if required <= present else Verdict("pass")

            reduced, report = reduce_trace(trace, oracle, max_calls=1000)
            self.assertTrue(report["minimality_verified"])
            self.assertEqual(oracle(reduced).status, "fail")
            # Independent fixed-point dependency removal, not Trace.prune.
            present = {int(r["id"]) for r in reduced["events"]}
            for removed in present:
                neighbor = present - {removed}
                for _ in range(size):
                    neighbor = {
                        i for i in neighbor if set(map(int, rows[i]["requires"])) <= neighbor
                    }
                self.assertFalse(required <= neighbor)


if __name__ == "__main__":
    unittest.main()
