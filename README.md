# FailShrink

[![Checks](https://github.com/brohum10/failshrink/actions/workflows/ci.yml/badge.svg)](https://github.com/brohum10/failshrink/actions/workflows/ci.yml)

**Turn a noisy failure trace into a small regression test.**

FailShrink removes irrelevant events while preserving the same observed failure
signature and the dependencies that make a replay meaningful. It runs your test
oracle, checks repeated observations, and reports exactly which reductions it
tried. Python 3.12+; standard library only at runtime.

The included synthetic example starts with **100 webhook events**. A deliberately
buggy ledger credits a retried payment twice. FailShrink retains these four:

```text
account-open → payment-first → payment-retry → balance-audit
      100 events → 4 events · same duplicate-payment-overcredit signature
```

That is a **96% event-count reduction on this authored fixture**, not a production
speedup or a general benchmark. The complete reference run is checked in under
[`evidence/reference/`](evidence/reference/summary.json): 124 oracle process calls,
two observations per evaluation, and a fresh final replay outside the cache.

## Run the actual demo

```bash
git clone https://github.com/brohum10/failshrink.git
cd failshrink
python3 examples/run_demo.py --out demo-run
```

The new directory contains `reduced.json`, `report.json`, and `summary.json`.
Existing directories are never overwritten. No installation, API key, server,
database or cloud account is needed for this example.

Replay the reduced case against both implementations:

```bash
python3 examples/ledger_oracle.py demo-run/reduced.json
# {"status": "fail", "signature": "duplicate-payment-overcredit"}
python3 examples/ledger_oracle.py demo-run/reduced.json --fixed
# {"status": "pass"}
```

The fixed example deduplicates payment IDs. Both implementations and all input
are small and inspectable; no real payment or customer data is used.

## Reduce your own trace

```bash
python3 -m failshrink examples/webhook-trace.json \
  --out run-ledger --repeats 2 --max-calls 400 --timeout 3 \
  --oracle python3 examples/ledger_oracle.py '{input}'
```

Put `--oracle` last: everything after it is the executable and its arguments.
Exactly one `{input}` placeholder is replaced with an absolute temporary JSON
path. Arguments are passed directly with `shell=False`, not evaluated by a shell.
Optionally install the `failshrink` command with `python3 -m pip install -e .`.

**Only run oracle commands you trust.** They execute with your account's
permissions. A temporary input file and a timeout are not a security sandbox.
Each invocation must reset its own application/database state.

### Input contract

```json
{
  "version": 1,
  "events": [
    {"id": "session", "op": "open"},
    {"id": "request", "requires": ["session"], "op": "send", "payload": {"value": 42}}
  ]
}
```

- `id` is unique, 1–128 ASCII letters, digits or `_ . : -`.
- `requires` is optional and names unique **earlier** event IDs. Forward edges,
  cycles, missing references and self-references are rejected, not silently fixed.
- Other event fields are application data. Their canonical JSON values and order
  are preserved; FailShrink removes events, not fields or payload fragments.
- Removing a prerequisite also removes every now-dangling dependent. Unrelated
  events stay in their original relative order.
- Input is bounded to 5,000,000 bytes and 10,000 events. Duplicate JSON keys,
  non-finite numbers, unsupported versions and invalid UTF-8 are rejected.

### Oracle protocol

Your command must exit **zero** and emit exactly one JSON object on stdout:

```json
{"status": "pass"}
```
```json
{"status": "fail", "signature": "stable-bug-identifier"}
```
```json
{"status": "unresolved"}
```

A nonzero exit, timeout, invalid protocol, or inconsistent repeated responses
is **unresolved**, never evidence of the desired bug. A different failure signature
cannot replace the original one. stderr is allowed but not interpreted as a
failure. Each captured stream is rejected above 64 KiB; output goes to temporary
files rather than an unbounded in-memory pipe. Those files are not disk quotas.

## Search and verification

```text
validate ordered dependency graph
  → establish one stable baseline signature
  → remove balanced chunks, pruning dangling dependents
  → memoize exact candidate tuples
  → test every single-event deletion plus dependent pruning
  → replay the final candidate again without using the cache
  → write reduced trace + audit report
```

The search is a complement-based delta-debugging variant. The final audit checks
the local neighborhood explicitly, including when failure behavior is
non-monotonic. Dependency pruning takes `O(events + dependency edges)` per
candidate and does not use recursion. Oracle work usually dominates; no universal
speedup or subquadratic oracle-call guarantee is claimed.

**`minimality_verified` has a precise scope:** deleting any one retained event,
then pruning its dependent events, did not reproduce the target failure in the
configured repeated checks. It is not a globally smallest trace, a proof of
determinism, or proof that the removed events are never relevant to another bug.
An unresolved neighbor prevents the claim. The evaluation budget reserves enough
calls for a fresh final replay even if reduction stops early.

| Exit | Meaning |
|---|---|
| `0` | Complete audit, same signature on fresh final replay |
| `2` | Invalid options/input or a file/output error |
| `3` | Original input did not establish one stable failure |
| `4` | Budget exhausted; partial case/report, no minimality claim |
| `5` | Inconclusive local audit or unstable final replay |

Reports include retained/removed IDs, canonical trace hashes, per-candidate
observations, cache statistics and deletion witnesses. They omit oracle stdout,
stderr, executable paths and command arguments. The reduced trace itself contains
payloads: do not publish private traces or sensitive IDs.

## Verify and reproduce

```bash
python3 -m unittest discover -v
python3 scripts/verify_reference.py
```

There are **37 automated tests**, including:

- 200 seeded dependency graphs compared with an independent fixed-point closure;
- 200 dependency-constrained monotonic oracles and 200 non-monotonic failure
  tables checked against independently calculated deletion neighbors;
- real subprocess and CLI tests for timeouts, malformed/oversized output,
  wrong signatures, flaky observations, budget exhaustion and existing output;
- a 2,500-event chain, proving traversal does not depend on Python recursion;
- fresh final replay, the buggy/fixed example, and all proper subsets of its
  four-event reproducer.

CI runs on Python 3.12 and 3.14 on Linux and Windows, and regenerates the reference
demo. POSIX process-group cleanup is tested locally/on Linux; Windows terminates
only the direct child and skips the descendant-cleanup test. These are software
checks on small fixtures, not operational validation against external systems.

## Boundaries

This is a local CLI, not hosted automation. Oracles may perform arbitrary I/O and
are not sandboxed, memory-limited, network-isolated or guaranteed reproducible.
POSIX descendants remaining in the owned process group are terminated; processes
that deliberately escape it are outside that protection. No disk or CPU quota is
enforced. Small repeated samples can miss intermittent behavior. Keep signatures
specific enough that two unrelated bugs cannot accidentally share one.

The examples demonstrate a known idempotency bug, not fault localization of an
unknown production incident. The library does not infer missing prerequisites or
generate tests from natural language.

## Attribution

The underlying reduction idea is delta debugging, not new research. See Zeller
and Hildebrandt, [*Simplifying and Isolating Failure-Inducing Input* (2002)](https://www.st.cs.uni-saarland.de/publications/details/zeller-tse-2002/),
and Zeller's [*Reducing Failure-Inducing Inputs*](https://www.debuggingbook.org/html/DeltaDebugger.html).
This implementation adds an explicit ordered-event dependency contract, structured
oracle protocol, conservative unresolved handling and an inspectable replay audit.
Developed with AI assistance. MIT licensed.
