import sys
from datetime import datetime, timezone

from eval.runner import run_all
from eval.scorer import EvalResult

_W = 70


def _check(passed: bool) -> str:
    return "✓" if passed else "✗"


def _print_report(results: list[EvalResult]) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"\nPayInvestigator Eval Harness — {now}")
    print("━" * _W)
    print(
        f"{'Scenario':<22} {'Routing':<10} {'Structure':<12} "
        f"{'Keywords':<11} {'Confidence':<13} Result"
    )
    print("━" * _W)
    for r in results:
        conf = f"{r.confidence:.2f}" if r.confidence is not None else "N/A"
        print(
            f"{r.scenario:<22} {_check(r.routing_pass):<10} "
            f"{_check(r.structure_pass):<12} {_check(r.keyword_pass):<11} "
            f"{conf:<13} {'PASS' if r.passed else 'FAIL'}"
        )
    print("━" * _W)
    passed_count = sum(1 for r in results if r.passed)
    print(f"{passed_count}/{len(results)} scenarios passed\n")

    for r in results:
        if not r.passed:
            print(f"[{r.scenario}] failures:")
            for err in r.errors:
                print(f"  • {err}")
            print()

    return 0 if passed_count == len(results) else 1


def main() -> None:
    print("Running eval suite (real Bedrock calls — ~60-90s)...")
    results = run_all()
    sys.exit(_print_report(results))


if __name__ == "__main__":
    main()
