import asyncio
import json
import sys
import os
from pathlib import Path
from unittest.mock import patch

# Ensure backend/ is on sys.path when run as a module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.graph import build_graph, make_llm
from eval.scorer import EvalResult, score

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(path: Path) -> tuple[dict, dict]:
    """Load a fixture file. Returns (meta, state) with _meta stripped from state."""
    raw = json.loads(path.read_text())
    meta = raw.pop("_meta")
    return meta, raw


async def _invoke(state: dict) -> dict:
    llm = make_llm()
    graph = build_graph(llm)
    return await graph.ainvoke(state)


def run_fixture(path: Path) -> EvalResult:
    """Run a single fixture through the full graph and return a scored EvalResult."""
    meta, state = load_fixture(path)
    with (
        patch("agents.tools.payment_tools.get_db", return_value=None),
        patch("agents.tools.technical_tools.get_db", return_value=None),
    ):
        final_state = asyncio.run(_invoke(state))
    return score(meta, final_state)


def run_all() -> list[EvalResult]:
    """Run all fixtures in alphabetical order. Returns one EvalResult per fixture."""
    results = []
    for fixture_path in sorted(FIXTURES_DIR.glob("*.json")):
        print(f"  Running {fixture_path.stem}...", flush=True)
        results.append(run_fixture(fixture_path))
    return results
