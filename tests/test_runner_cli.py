import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from failshrink import Trace
from failshrink.runner import CommandOracle

ROOT = Path(__file__).resolve().parents[1]
EMPTY = {"version": 1, "events": []}


def command(code, *arguments, timeout=3):
    return CommandOracle([sys.executable, "-c", code, *arguments, "{input}"], timeout=timeout)


class RunnerTests(unittest.TestCase):
    def test_valid_pass_failure_and_unresolved(self):
        for response in [
            {"status": "pass"},
            {"status": "fail", "signature": "x"},
            {"status": "unresolved"},
        ]:
            result = command(f"print({json.dumps(json.dumps(response))})")(EMPTY)
            self.assertEqual(result.status, response["status"])
            self.assertEqual(result.signature, response.get("signature", ""))

    def test_exit_codes_do_not_masquerade_as_target_failure(self):
        result = command('print(\'{"status":"fail","signature":"x"}\'); raise SystemExit(7)')(EMPTY)
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(result.reason, "exit:7")

    def test_malformed_protocols_are_unresolved(self):
        for output in [
            "broken",
            "[]",
            '{"status":"fail"}',
            '{"status":"pass","signature":"x"}',
            '{"status":"fail","signature":42}',
            '{"status":"other"}',
            '{"status":"pass","debug":"extra"}',
            '{"status":"pass","status":"fail"}',
            '{"status":"pass"}\n{"status":"pass"}',
        ]:
            with self.subTest(output=output):
                result = command(f"print({output!r})")(EMPTY)
                self.assertEqual(result.reason, "invalid_protocol")

    def test_invalid_encoding_is_unresolved(self):
        result = command("import sys; sys.stdout.buffer.write(b'\\xff')")(EMPTY)
        self.assertEqual(result.reason, "invalid_protocol")

    def test_output_limits_apply_to_both_streams(self):
        for code in ["print('x'*65537)", "import sys; print('x'*65537,file=sys.stderr)"]:
            self.assertEqual(command(code)(EMPTY).reason, "output_limit")

    def test_timeout_returns_unresolved(self):
        self.assertEqual(
            command("import time; time.sleep(5)", timeout=0.05)(EMPTY).reason, "timeout"
        )

    def test_placeholder_and_option_validation(self):
        for argv in [
            [],
            ["python"],
            ["python", "{input}", "{input}"],
            ["", "{input}"],
            ["bad\0", "{input}"],
        ]:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                CommandOracle(argv)
        for timeout in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                command("pass", timeout=timeout)

    def test_arguments_are_literal_not_shell_interpolated(self):
        literal = "a; $(echo injected) & spaces"
        code = f"import sys,json; assert sys.argv[1]=={literal!r}; print(json.dumps({{'status':'pass'}}))"
        self.assertEqual(command(code, literal)(EMPTY).status, "pass")

    def test_candidate_file_is_valid_and_removed_after_run(self):
        with tempfile.TemporaryDirectory() as folder:
            captured = Path(folder) / "path.txt"
            code = (
                "import sys,json,pathlib; p=pathlib.Path(sys.argv[-1]); "
                "assert json.loads(p.read_text())=={'version':1,'events':[]}; "
                f"pathlib.Path({str(captured)!r}).write_text(str(p)); "
                "print(json.dumps({'status':'pass'}))"
            )
            self.assertEqual(command(code)(EMPTY).status, "pass")
            self.assertFalse(Path(captured.read_text()).exists())

    @unittest.skipUnless(os.name == "posix", "process-group descendant cleanup is POSIX-only")
    def test_timeout_terminates_owned_descendants(self):
        with tempfile.TemporaryDirectory() as folder:
            started, completed = Path(folder) / "started", Path(folder) / "completed"
            child = (
                f"from pathlib import Path; import time; Path({str(started)!r}).touch(); "
                f"time.sleep(1); Path({str(completed)!r}).touch()"
            )
            parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(5)"
            self.assertEqual(command(parent, timeout=0.5)(EMPTY).reason, "timeout")
            self.assertTrue(
                started.exists(), "child must actually have started for this check to be meaningful"
            )
            time.sleep(1.2)
            self.assertFalse(completed.exists())


class CliTests(unittest.TestCase):
    def run_cli(self, trace, output, *flags, code=None):
        oracle = (
            [sys.executable, "-c", code, "{input}"]
            if code is not None
            else [sys.executable, str(ROOT / "examples/ledger_oracle.py"), "{input}"]
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "failshrink",
                str(trace),
                "--out",
                str(output),
                *flags,
                "--oracle",
                *oracle,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_real_cli_end_to_end_and_existing_output_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "result with spaces"
            result = self.run_cli(ROOT / "examples/webhook-trace.json", out)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((out / "report.json").read_text())
            self.assertTrue(report["minimality_verified"])
            self.assertEqual(report["reduced_count"], 4)
            Trace.load(out / "reduced.json")
            before = (out / "report.json").read_bytes()
            self.assertEqual(self.run_cli(ROOT / "examples/webhook-trace.json", out).returncode, 2)
            self.assertEqual((out / "report.json").read_bytes(), before)

    def test_budget_exit_and_replay_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "budget"
            result = self.run_cli(ROOT / "examples/webhook-trace.json", out, "--max-calls", "4")
            self.assertEqual(result.returncode, 4, result.stderr)
            report = json.loads((out / "report.json").read_text())
            self.assertTrue(report["fresh_final_replay"])
            self.assertFalse(report["minimality_verified"])

    def test_invalid_input_has_no_output_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "bad.json", Path(folder) / "out"
            source.write_text('{"version":1,"version":2}')
            self.assertEqual(self.run_cli(source, out).returncode, 2)
            self.assertFalse(out.exists())

    def test_original_passing_oracle_exits_three(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "out"
            result = self.run_cli(
                ROOT / "examples/webhook-trace.json", out, code='print(\'{"status":"pass"}\')'
            )
            self.assertEqual(result.returncode, 3)
            self.assertFalse(out.exists())

    def test_unresolved_minimality_exits_five(self):
        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "one.json", Path(folder) / "out"
            source.write_text('{"version":1,"events":[{"id":"a"}]}')
            code = (
                "import sys,json; d=json.load(open(sys.argv[1])); "
                "print(json.dumps({'status':'fail','signature':'x'} if d['events'] else {'status':'unresolved'}))"
            )
            self.assertEqual(self.run_cli(source, out, code=code).returncode, 5)


if __name__ == "__main__":
    unittest.main()
