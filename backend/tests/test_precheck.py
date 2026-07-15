import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.nodes.intake import intake_node


@pytest.mark.asyncio
async def test_intake_node_returns_usage_metadata():
    mock_response = MagicMock()
    mock_response.content = "Bad IBAN checksum detected. Routing to Technical Diagnosis."
    mock_response.usage_metadata = {"input_tokens": 120, "output_tokens": 45, "total_tokens": 165}

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    state = {
        "payment": {"id": 1, "msg_id": "MSG001", "uetr": "abc", "amount": "1000",
                    "currency": "EUR", "sender_bic": "BICABC", "receiver_bic": "BICDEF",
                    "debtor_bic": None, "creditor_bic": None, "debtor_name": "Alice",
                    "debtor_iban": "DE89370400440532013000", "creditor_name": "Bob",
                    "creditor_iban": "GB29NWBK60161331926819"},
        "detected_errors": [{"code": "IBAN_INVALID_CHECKSUM", "field": "creditor_iban", "value": "bad"}],
        "swift_message": "", "intake_classification": {}, "investigation_context": {},
        "technical_findings": None, "compliance_findings": None,
        "recommendation": None, "steps": [], "investigation_id": None, "msg_id": "MSG001",
    }

    result = await intake_node(state, mock_llm)

    assert "usage_metadata" in result
    assert result["usage_metadata"]["input_tokens"] == 120
    assert result["usage_metadata"]["output_tokens"] == 45
    assert "intake_classification" in result
    assert result["intake_classification"]["needs_technical"] is True
