# Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-surface eval harness — fast mocked routing tests in pytest, plus a standalone `eval/` suite that runs the real graph against 3 demo scenarios and prints a scored pass/fail table.

**Architecture:** `eval/scorer.py` holds `EvalResult` and `score()`; `eval/runner.py` loads fixtures, patches DB, and drives `build_graph()`; `eval/report.py` is the CLI entry point. Fast routing tests live in `tests/test_agent_routing.py`, patching `ChatBedrock` entirely.

**Tech Stack:** Python standard library only (`asyncio`, `unittest.mock`, `dataclasses`, `json`, `pathlib`). No new dependencies.

## Global Constraints

- All new files live under `backend/` — no changes outside that directory.
- `from db import get_db` appears in `agents/tools/payment_tools.py` AND `agents/tools/technical_tools.py`; both must be patched in any test or eval run that doesn't have a live DB.
- `compliance_tools.py` has no DB dependency — no patching needed there.
- Routing is determined by `ERROR_CATEGORY_MAP` in `agents/state.py` — it is deterministic and does not depend on LLM output.
- `graph.ainvoke()` is async; call it with `asyncio.run()` from sync test/runner code.
- `check_duplicate_tool` signature: `(uetr: str, msg_id: str)` — both fields must be present in any payment fixture that includes a `DUPLICATE_UETR` error.
- `CANNED_RECOMMENDATION` used in routing tests must be valid JSON parseable by `resolution_node` with all four required keys.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/eval/__init__.py` | Create | Empty — makes `eval` a package |
| `backend/eval/scorer.py` | Create | `EvalResult` dataclass + `score()` function |
| `backend/eval/fixtures/bad_iban.json` | Create | Demo scenario 1 fixture |
| `backend/eval/fixtures/sanctions_hit.json` | Create | Demo scenario 2 fixture |
| `backend/eval/fixtures/duplicate_payment.json` | Create | Demo scenario 3 fixture |
| `backend/eval/runner.py` | Create | Loads fixtures, patches DB, drives graph, returns `EvalResult` list |
| `backend/eval/report.py` | Create | CLI entry point — prints scored table |
| `backend/tests/test_agent_routing.py` | Create | Mocked-LLM routing + structure tests |

---

### Task 1: EvalResult dataclass + scorer

**Files:**
- Create: `backend/eval/__init__.py`
- Create: `backend/eval/scorer.py`
- Test: inline verification step (no separate test file)

**Interfaces:**
- Produces: `EvalResult` dataclass; `score(meta: dict, final_state: dict) -> EvalResult` — used by Task 3 (runner)

- [ ] **Step 1: Create the package init**

  Create `backend/eval/__init__.py` — empty file.

  ```bash
  touch backend/eval/__init__.py
  ```

- [ ] **Step 2: Write `scorer.py`**

  Create `backend/eval/scorer.py`:

  ```python
  from dataclasses import dataclass, field
  from typing import Optional


  @dataclass
  class EvalResult:
      scenario: str
      routing_pass: bool = False
      structure_pass: bool = False
      keyword_pass: bool = False
      confidence: Optional[float] = None
      errors: list = field(default_factory=list)

      @property
      def passed(self) -> bool:
          return self.routing_pass and self.structure_pass and self.keyword_pass


  def score(meta: dict, final_state: dict) -> EvalResult:
      result = EvalResult(scenario=meta["scenario"])

      # Check 1: Routing
      classification = final_state.get("intake_classification") or {}
      expected = meta["expected_routing"]
      if (classification.get("needs_technical") == expected["needs_technical"] and
              classification.get("needs_compliance") == expected["needs_compliance"]):
          result.routing_pass = True
      else:
          result.errors.append(
              f"Routing mismatch: expected {expected}, got "
              f"technical={classification.get('needs_technical')} "
              f"compliance={classification.get('needs_compliance')}"
          )

      # Check 2: Structure
      rec = final_state.get("recommendation") or {}
      required_keys = {"action", "rationale", "confidence", "requires_human_approval"}
      missing = required_keys - set(rec.keys())
      if not missing and rec.get("requires_human_approval") is True:
          result.structure_pass = True
      else:
          if missing:
              result.errors.append(f"Missing recommendation keys: {missing}")
          if rec.get("requires_human_approval") is not True:
              result.errors.append(
                  f"requires_human_approval={rec.get('requires_human_approval')!r}, expected True"
              )

      # Check 3: Keywords + Confidence
      confidence = rec.get("confidence")
      result.confidence = float(confidence) if confidence is not None else None

      text = f"{rec.get('action', '')} {rec.get('rationale', '')}".lower()
      keywords = meta.get("required_keywords", [])
      keyword_hit = any(kw.lower() in text for kw in keywords)
      min_conf = meta.get("min_confidence", 0.75)
      confidence_ok = result.confidence is not None and result.confidence >= min_conf

      if keyword_hit and confidence_ok:
          result.keyword_pass = True
      else:
          if not keyword_hit:
              result.errors.append(
                  f"None of required keywords {keywords} found in recommendation text"
              )
          if not confidence_ok:
              result.errors.append(
                  f"Confidence {result.confidence} below threshold {min_conf}"
              )

      return result
  ```

- [ ] **Step 3: Verify scorer logic with a quick inline check**

  Run from `backend/`:

  ```bash
  python -c "
  from eval.scorer import score

  # Should PASS all 3 checks
  meta = {
      'scenario': 'test',
      'expected_routing': {'needs_technical': True, 'needs_compliance': False},
      'required_keywords': ['IBAN'],
      'min_confidence': 0.75,
  }
  state = {
      'intake_classification': {'needs_technical': True, 'needs_compliance': False},
      'recommendation': {
          'action': 'Correct the IBAN and resubmit',
          'rationale': 'IBAN checksum is invalid',
          'confidence': 0.9,
          'requires_human_approval': True,
      },
  }
  r = score(meta, state)
  assert r.passed, f'Expected PASS, got errors: {r.errors}'
  print('scorer OK')
  "
  ```

  Expected output: `scorer OK`

- [ ] **Step 4: Commit**

  ```bash
  git add backend/eval/__init__.py backend/eval/scorer.py
  git commit -m "feat: eval harness — EvalResult dataclass and scorer"
  ```

---

### Task 2: Fixtures

**Files:**
- Create: `backend/eval/fixtures/bad_iban.json`
- Create: `backend/eval/fixtures/sanctions_hit.json`
- Create: `backend/eval/fixtures/duplicate_payment.json`

**Interfaces:**
- Produces: JSON fixture files consumed by `runner.py` (Task 3). The `_meta` key is stripped by the runner before passing state to the graph.

- [ ] **Step 1: Create `bad_iban.json`**

  Create `backend/eval/fixtures/bad_iban.json`:

  ```json
  {
    "_meta": {
      "scenario": "bad_iban",
      "expected_routing": { "needs_technical": true, "needs_compliance": false },
      "required_keywords": ["IBAN", "correct", "resubmit"],
      "min_confidence": 0.75
    },
    "payment": {
      "msg_id": "EVAL-IBAN-001",
      "amount": 10000.00,
      "currency": "USD",
      "sender_bic": "DEUTDEDB",
      "receiver_bic": "NWBKGB2L",
      "debtor_bic": "DEUTDEDB",
      "creditor_bic": "NWBKGB2L",
      "debtor_name": "Acme GmbH",
      "debtor_iban": "DE89370400440532013000",
      "creditor_name": "Thames Logistics Ltd",
      "creditor_iban": "GB29NWBK60161331926820",
      "is_faulty": true,
      "ingested_at": "2026-07-14T08:00:00Z"
    },
    "detected_errors": [
      {
        "code": "IBAN_INVALID_CHECKSUM",
        "field": "creditor_iban",
        "value": "GB29NWBK60161331926820"
      }
    ],
    "swift_message": "<pacs.008 stub — EVAL-IBAN-001>",
    "steps": [],
    "investigation_id": 101,
    "msg_id": "EVAL-IBAN-001",
    "intake_classification": {},
    "investigation_context": {},
    "technical_findings": null,
    "compliance_findings": null,
    "recommendation": null
  }
  ```

  Note: `GB29NWBK60161331926820` has a bad checksum (last digit changed from 9 to 0). The valid IBAN is `GB29NWBK60161331926819`.

- [ ] **Step 2: Create `sanctions_hit.json`**

  Create `backend/eval/fixtures/sanctions_hit.json`:

  ```json
  {
    "_meta": {
      "scenario": "sanctions_hit",
      "expected_routing": { "needs_technical": false, "needs_compliance": true },
      "required_keywords": ["hold", "sanction"],
      "min_confidence": 0.80
    },
    "payment": {
      "msg_id": "EVAL-SANC-001",
      "amount": 250000.00,
      "currency": "USD",
      "sender_bic": "BARCGB22",
      "receiver_bic": "DEUTDEDB",
      "debtor_bic": "BARCGB22",
      "creditor_bic": "DEUTDEDB",
      "debtor_name": "Barclays Client UK",
      "debtor_iban": "GB82WEST12345698765432",
      "creditor_name": "Novaya Star Shipping",
      "creditor_iban": "DE89370400440532013000",
      "is_faulty": true,
      "ingested_at": "2026-07-14T08:05:00Z"
    },
    "detected_errors": [
      {
        "code": "BENEFICIARY_NAME_INCOMPLETE",
        "field": "creditor_name",
        "value": "Novaya Star Shipping"
      }
    ],
    "swift_message": "<pacs.008 stub — EVAL-SANC-001>",
    "steps": [],
    "investigation_id": 102,
    "msg_id": "EVAL-SANC-001",
    "intake_classification": {},
    "investigation_context": {},
    "technical_findings": null,
    "compliance_findings": null,
    "recommendation": null
  }
  ```

  Note: "Novaya Star Shipping" fuzzy-matches "NOVAYA ZVEZDA SHIPPING LLC" in `compliance_tools.SDN_LIST` with score ≥ 0.70, triggering a sanctions hit in `screen_entity_tool`.

- [ ] **Step 3: Create `duplicate_payment.json`**

  Create `backend/eval/fixtures/duplicate_payment.json`:

  ```json
  {
    "_meta": {
      "scenario": "duplicate_payment",
      "expected_routing": { "needs_technical": true, "needs_compliance": false },
      "required_keywords": ["duplicate", "cancel"],
      "min_confidence": 0.75
    },
    "payment": {
      "msg_id": "EVAL-DUP-002",
      "uetr": "a8f3c1d2-4e5f-6789-abcd-ef0123456789",
      "amount": 5000.00,
      "currency": "EUR",
      "sender_bic": "BNPAFRPP",
      "receiver_bic": "INGBNL2A",
      "debtor_bic": "BNPAFRPP",
      "creditor_bic": "INGBNL2A",
      "debtor_name": "BNP Client FR",
      "debtor_iban": "FR7630006000011234567890189",
      "creditor_name": "ING Client NL",
      "creditor_iban": "NL91ABNA0417164300",
      "is_faulty": true,
      "ingested_at": "2026-07-14T08:10:00Z"
    },
    "detected_errors": [
      {
        "code": "DUPLICATE_UETR",
        "field": "uetr",
        "value": "a8f3c1d2-4e5f-6789-abcd-ef0123456789"
      }
    ],
    "swift_message": "<pacs.008 stub — EVAL-DUP-002>",
    "steps": [],
    "investigation_id": 103,
    "msg_id": "EVAL-DUP-002",
    "intake_classification": {},
    "investigation_context": {},
    "technical_findings": null,
    "compliance_findings": null,
    "recommendation": null
  }
  ```

  Note: `uetr` and `msg_id` are both present so the real `check_duplicate_tool(uetr, msg_id)` can be called. With DB unavailable, it returns `{"duplicate": false, "error": "DB unavailable"}` — the LLM still sees `DUPLICATE_UETR` in `detected_errors` and reasons accordingly.

- [ ] **Step 4: Validate all fixtures parse as JSON**

  Run from `backend/`:

  ```bash
  python -c "
  import json
  from pathlib import Path
  for p in Path('eval/fixtures').glob('*.json'):
      data = json.loads(p.read_text())
      assert '_meta' in data, f'Missing _meta in {p.name}'
      assert 'payment' in data, f'Missing payment in {p.name}'
      assert 'detected_errors' in data, f'Missing detected_errors in {p.name}'
      print(f'  {p.name} OK — scenario={data[\"_meta\"][\"scenario\"]}')
  print('All fixtures valid')
  "
  ```

  Expected output:
  ```
    bad_iban.json OK — scenario=bad_iban
    duplicate_payment.json OK — scenario=duplicate_payment
    sanctions_hit.json OK — scenario=sanctions_hit
  All fixtures valid
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add backend/eval/fixtures/
  git commit -m "feat: eval harness — fixtures for 3 demo scenarios"
  ```

---

### Task 3: Runner

**Files:**
- Create: `backend/eval/runner.py`

**Interfaces:**
- Consumes: `score(meta, final_state) -> EvalResult` from `eval.scorer`; `build_graph(llm)` and `make_llm()` from `agents.graph`
- Produces: `run_all() -> list[EvalResult]` — used by `report.py` (Task 4)

- [ ] **Step 1: Write `runner.py`**

  Create `backend/eval/runner.py`:

  ```python
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
  ```

- [ ] **Step 2: Smoke-test the runner against one fixture (dry-run import check)**

  Run from `backend/`:

  ```bash
  python -c "
  from eval.runner import load_fixture
  from pathlib import Path
  meta, state = load_fixture(Path('eval/fixtures/bad_iban.json'))
  assert meta['scenario'] == 'bad_iban'
  assert '_meta' not in state
  assert 'payment' in state
  print('runner import and load_fixture OK')
  "
  ```

  Expected output: `runner import and load_fixture OK`

- [ ] **Step 3: Commit**

  ```bash
  git add backend/eval/runner.py
  git commit -m "feat: eval harness — runner (loads fixtures, patches DB, drives graph)"
  ```

---

### Task 4: Report entry point

**Files:**
- Create: `backend/eval/report.py`

**Interfaces:**
- Consumes: `run_all() -> list[EvalResult]` from `eval.runner`; `EvalResult` from `eval.scorer`
- Produces: human-readable table on stdout; exit code 0 if all pass, 1 if any fail

- [ ] **Step 1: Write `report.py`**

  Create `backend/eval/report.py`:

  ```python
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
  ```

- [ ] **Step 2: Verify the module is importable and `_print_report` works with fake data**

  Run from `backend/`:

  ```bash
  python -c "
  from eval.report import _print_report
  from eval.scorer import EvalResult
  fake = [
      EvalResult('bad_iban', routing_pass=True, structure_pass=True, keyword_pass=True, confidence=0.92),
      EvalResult('sanctions_hit', routing_pass=True, structure_pass=True, keyword_pass=False, confidence=0.61,
                 errors=['None of required keywords found']),
  ]
  code = _print_report(fake)
  assert code == 1  # one failure
  print('report OK')
  "
  ```

  Expected: prints a table, then `report OK`.

- [ ] **Step 3: Commit**

  ```bash
  git add backend/eval/report.py
  git commit -m "feat: eval harness — report entry point (python -m eval.report)"
  ```

---

### Task 5: Fast routing tests

**Files:**
- Create: `backend/tests/test_agent_routing.py`

**Interfaces:**
- Consumes: `build_graph(llm)` from `agents.graph`; `InvestigationState` shape from `agents.state`
- Produces: pytest tests — run with `pytest tests/test_agent_routing.py`

- [ ] **Step 1: Write `test_agent_routing.py`**

  Create `backend/tests/test_agent_routing.py`:

  ```python
  import asyncio
  import json
  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest

  from agents.graph import build_graph

  # Valid JSON that resolution_node can parse — has all four required keys.
  _CANNED = json.dumps({
      "action": "Test action: correct and resubmit",
      "rationale": "Test rationale: error detected in payment",
      "confidence": 0.9,
      "requires_human_approval": True,
  })


  def _make_mock_llm() -> MagicMock:
      """
      LLM mock that:
      - Returns canned JSON for all ainvoke() calls.
      - Returns no tool_calls so technical/compliance ReAct loops exit after one iteration.
      - bind_tools() returns itself (technical/compliance nodes call llm.bind_tools(TOOLS)).
      """
      response = MagicMock()
      response.content = _CANNED
      response.tool_calls = []

      mock = MagicMock()
      mock.ainvoke = AsyncMock(return_value=response)
      mock.bind_tools = MagicMock(return_value=mock)
      return mock


  def _base_state(error_codes: list[str]) -> dict:
      return {
          "payment": {
              "msg_id": "TEST-001",
              "uetr": None,
              "amount": 1000.0,
              "currency": "USD",
              "sender_bic": "DEUTDEDB",
              "receiver_bic": "NWBKGB2L",
              "debtor_bic": "DEUTDEDB",
              "creditor_bic": "NWBKGB2L",
              "debtor_name": "Test Corp",
              "debtor_iban": "DE89370400440532013000",
              "creditor_name": "Test Beneficiary",
              "creditor_iban": "GB29NWBK60161331926819",
          },
          "detected_errors": [
              {"code": c, "field": "test_field", "value": "test_value"}
              for c in error_codes
          ],
          "swift_message": "<stub/>",
          "steps": [],
          "investigation_id": 1,
          "msg_id": "TEST-001",
          "intake_classification": {},
          "investigation_context": {},
          "technical_findings": None,
          "compliance_findings": None,
          "recommendation": None,
      }


  def _run(state: dict, llm: MagicMock) -> dict:
      graph = build_graph(llm)
      with (
          patch("agents.tools.payment_tools.get_db", return_value=None),
          patch("agents.tools.technical_tools.get_db", return_value=None),
      ):
          return asyncio.run(graph.ainvoke(state))


  @pytest.fixture
  def mock_llm() -> MagicMock:
      return _make_mock_llm()


  class TestRouting:
      def test_iban_checksum_error_routes_to_technical(self, mock_llm):
          result = _run(_base_state(["IBAN_INVALID_CHECKSUM"]), mock_llm)
          cls = result["intake_classification"]
          assert cls["needs_technical"] is True
          assert cls["needs_compliance"] is False

      def test_iban_wrong_length_routes_to_technical(self, mock_llm):
          result = _run(_base_state(["IBAN_WRONG_LENGTH"]), mock_llm)
          cls = result["intake_classification"]
          assert cls["needs_technical"] is True
          assert cls["needs_compliance"] is False

      def test_beneficiary_name_error_routes_to_compliance(self, mock_llm):
          result = _run(_base_state(["BENEFICIARY_NAME_INCOMPLETE"]), mock_llm)
          cls = result["intake_classification"]
          assert cls["needs_technical"] is False
          assert cls["needs_compliance"] is True

      def test_duplicate_uetr_routes_to_technical(self, mock_llm):
          result = _run(_base_state(["DUPLICATE_UETR"]), mock_llm)
          cls = result["intake_classification"]
          assert cls["needs_technical"] is True
          assert cls["needs_compliance"] is False

      def test_unknown_error_defaults_to_technical(self, mock_llm):
          result = _run(_base_state(["COMPLETELY_UNKNOWN_CODE"]), mock_llm)
          cls = result["intake_classification"]
          # intake_node falls back to account_identifier for unmapped codes,
          # which is in TECHNICAL_CATEGORIES — so needs_technical=True
          assert cls["needs_technical"] is True


  class TestStructure:
      def test_recommendation_has_all_required_keys(self, mock_llm):
          result = _run(_base_state(["IBAN_INVALID_CHECKSUM"]), mock_llm)
          rec = result["recommendation"]
          assert rec is not None, "recommendation must not be None"
          for key in ("action", "rationale", "confidence", "requires_human_approval"):
              assert key in rec, f"Missing required key in recommendation: '{key}'"

      def test_requires_human_approval_always_true(self, mock_llm):
          for error_code in [
              "IBAN_INVALID_CHECKSUM",
              "BENEFICIARY_NAME_INCOMPLETE",
              "DUPLICATE_UETR",
          ]:
              llm = _make_mock_llm()
              result = _run(_base_state([error_code]), llm)
              rec = result.get("recommendation") or {}
              assert rec.get("requires_human_approval") is True, (
                  f"requires_human_approval is not True for error_code={error_code!r}; "
                  f"got recommendation={rec!r}"
              )

      def test_steps_are_populated_and_well_formed(self, mock_llm):
          result = _run(_base_state(["IBAN_INVALID_CHECKSUM"]), mock_llm)
          steps = result.get("steps", [])
          assert len(steps) > 0, "steps list must not be empty"
          for step in steps:
              for field in ("agent", "cls", "text", "ts"):
                  assert field in step, (
                      f"Step missing field '{field}'. Step: {step!r}"
                  )
  ```

- [ ] **Step 2: Run the tests and verify they all pass**

  Run from `backend/`:

  ```bash
  pytest tests/test_agent_routing.py -v
  ```

  Expected output:
  ```
  tests/test_agent_routing.py::TestRouting::test_iban_checksum_error_routes_to_technical PASSED
  tests/test_agent_routing.py::TestRouting::test_iban_wrong_length_routes_to_technical PASSED
  tests/test_agent_routing.py::TestRouting::test_beneficiary_name_error_routes_to_compliance PASSED
  tests/test_agent_routing.py::TestRouting::test_duplicate_uetr_routes_to_technical PASSED
  tests/test_agent_routing.py::TestRouting::test_unknown_error_defaults_to_technical PASSED
  tests/test_agent_routing.py::TestStructure::test_recommendation_has_all_required_keys PASSED
  tests/test_agent_routing.py::TestStructure::test_requires_human_approval_always_true PASSED
  tests/test_agent_routing.py::TestStructure::test_steps_are_populated_and_well_formed PASSED
  8 passed
  ```

  If any fail, check:
  - `asyncio.run()` nesting: each test creates its own event loop via `asyncio.run()` — this works in Python 3.7+ when called from a sync context.
  - Patch targets: confirm `get_db` is imported at module level in both `payment_tools.py` and `technical_tools.py`.

- [ ] **Step 3: Run the full existing test suite to confirm no regressions**

  ```bash
  pytest tests/ -v
  ```

  Expected: all existing `test_tools.py` tests still pass alongside the new routing tests.

- [ ] **Step 4: Commit**

  ```bash
  git add backend/tests/test_agent_routing.py
  git commit -m "feat: eval harness — fast routing and structure tests (mocked LLM)"
  ```

---

## Running the full eval suite

After all tasks are complete, run the real-LLM eval from `backend/`:

```bash
python -m eval.report
```

Requires live AWS Bedrock credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`). Takes ~60–90 seconds. Exit code 0 = all scenarios pass.

## Quick reference

| Command | What it tests | LLM calls | Time |
|---|---|---|---|
| `pytest tests/` | Tools + routing + structure | None (mocked) | ~5s |
| `python -m eval.report` | Full graph end-to-end for 3 demo scenarios | 3 real graph runs | ~90s |
