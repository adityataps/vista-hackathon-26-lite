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
