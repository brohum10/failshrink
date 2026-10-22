"""Run an explicitly chosen, trusted oracle without a shell."""

import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile

from .core import Verdict, canonical, strict_json

MAX_OUTPUT = 65_536


class CommandOracle:
    def __init__(self, argv, *, timeout=3.0):
        if not argv or any(type(x) is not str or not x or "\0" in x for x in argv):
            raise ValueError("oracle command needs nonempty arguments")
        if sum(x.count("{input}") for x in argv) != 1:
            raise ValueError("oracle command must contain exactly one {input} placeholder")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.argv, self.timeout = tuple(argv), timeout

    def __call__(self, document):
        with tempfile.TemporaryDirectory(prefix="failshrink-") as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(canonical(document), encoding="utf-8")
            args = [a.replace("{input}", str(path)) for a in self.argv]
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=(os.name == "posix"),
                    shell=False,
                )
                timeout = False
                try:
                    process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    timeout = True
                finally:
                    # On POSIX also terminate descendants left in the owned process group.
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    elif process.poll() is None:
                        process.kill()
                    process.wait()
                if timeout:
                    return Verdict("unresolved", reason="timeout")
                if process.returncode:
                    return Verdict("unresolved", reason=f"exit:{process.returncode}")
                if stdout.tell() > MAX_OUTPUT or stderr.tell() > MAX_OUTPUT:
                    return Verdict("unresolved", reason="output_limit")
                stdout.seek(0)
                try:
                    result = strict_json(stdout.read(MAX_OUTPUT + 1).decode("utf-8"))
                    if type(result) is not dict or set(result) - {"status", "signature"}:
                        raise ValueError("oracle response fields")
                    if (
                        type(result.get("status")) is not str
                        or type(result.get("signature", "")) is not str
                    ):
                        raise ValueError("oracle response types")
                    return Verdict(result["status"], result.get("signature", ""))
                except (ValueError, KeyError, UnicodeError, RecursionError):
                    return Verdict("unresolved", reason="invalid_protocol")
