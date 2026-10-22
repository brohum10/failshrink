"""Re-run the actual subprocess example and compare its deterministic artifacts."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="failshrink-check-") as folder:
        out = Path(folder) / "run"
        result = subprocess.run(
            [sys.executable, str(ROOT / "examples/run_demo.py"), "--out", str(out)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        for name in ["reduced.json", "report.json", "summary.json"]:
            actual = json.loads((out / name).read_text())
            expected = json.loads((ROOT / "evidence/reference" / name).read_text())
            assert actual == expected, f"reference drift: {name}"
        reduced = out / "reduced.json"

        def replay(*options):
            return json.loads(
                subprocess.check_output(
                    [
                        sys.executable,
                        str(ROOT / "examples/ledger_oracle.py"),
                        str(reduced),
                        *options,
                    ],
                    text=True,
                )
            )

        assert replay() == {"status": "fail", "signature": "duplicate-payment-overcredit"}
        assert replay("--fixed") == {"status": "pass"}
        print(
            "Verified: exact 100 -> 4 reduction, full audit, buggy replay fails, fixed replay passes."
        )


if __name__ == "__main__":
    main()
