# Checks every figure quoted in README.md and QUALITY-ATTRIBUTES.md against the four report files.
#
# The constitution requires quoted numbers to be checked rather than recalled, and mechanically where practical. This recomputes each figure from the reports and asserts the exact string appears in the write-up, so a number that drifts after a re-run fails here rather than in front of a reader.
# Exits non-zero on any mismatch. Run from event-driven/: uv run python scripts/check_figures.py
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "QUALITY-ATTRIBUTES.md"]


def load(pattern: str) -> dict[int, dict]:
    """Reads the reports matching a pattern, keyed by how many stations the run used."""
    runs = {}
    for name in glob.glob(str(ROOT / pattern)):
        report = json.load(open(name))
        runs[report["config"]["stations"]] = report
    return runs


def figures(report: dict) -> dict[str, str]:
    """Turns one report into the exact strings the write-ups are allowed to quote."""
    ops = {o["operation"]: o for o in report["operations"]}
    return {
        "transactions/sec": f"{report['transactionsPerSecond']:.1f}",
        "items/sec": f"{report['itemsPerSecond']:,.1f}",
        "START p95": f"{ops['START_TRANSACTION']['p95Ms']:.2f}ms",
        "SCAN p95": f"{ops['SCAN_ITEM']['p95Ms']:.2f}ms",
        "COMPLETE p95": f"{ops['COMPLETE_TRANSACTION']['p95Ms']:.2f}ms",
    }


def main() -> None:
    nginx = load("reports/report-*.json")
    python_edge = load("reports/comparison/report-*.json")

    if sorted(nginx) != [10, 100] or sorted(python_edge) != [10, 100]:
        sys.exit(f"expected one 10-station and one 100-station run in each folder, found {sorted(nginx)} and {sorted(python_edge)}")

    text = "\n".join((ROOT / d).read_text() for d in DOCS)

    missing = []
    for edge, runs in (("nginx", nginx), ("python gateway", python_edge)):
        for stations, report in sorted(runs.items()):
            for label, value in figures(report).items():
                if value not in text:
                    missing.append(f"{edge}, {stations} stations: {label} is {value}, which appears nowhere in the write-ups")

    # The operation total is quoted as one number and is the easiest thing in the write-up to get wrong by hand.
    total = sum(
        op["successCount"] + op["errorCount"]
        for runs in (nginx, python_edge)
        for report in runs.values()
        for op in report["operations"]
    )
    if f"{total:,}" not in text:
        missing.append(f"the total operation count is {total:,}, which appears nowhere in the write-ups")

    # A single error anywhere would make every "0.00%" and "zero errors" claim false.
    errors = sum(
        op["errorCount"]
        for runs in (nginx, python_edge)
        for report in runs.values()
        for op in report["operations"]
    )
    if errors and "zero errors" in text:
        missing.append(f"the write-ups claim zero errors, but the reports hold {errors}")

    for line in missing:
        print(line)
    print(f"\n{len(missing)} mismatch(es) against {len(nginx) + len(python_edge)} reports")
    sys.exit(1 if missing else 0)


main()
