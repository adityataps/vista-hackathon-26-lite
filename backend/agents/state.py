from typing import Optional, TypedDict


class InvestigationState(TypedDict):
    payment: dict
    detected_errors: list          # [{code, field, value}]
    swift_message: str
    intake_classification: dict    # {error_categories: [], needs_technical: bool, needs_compliance: bool}
    investigation_context: dict
    technical_findings: Optional[dict]
    compliance_findings: Optional[dict]
    recommendation: Optional[dict] # {action, rationale, confidence}
    steps: list                    # append-only [{agent, cls, text, ts}]
    investigation_id: Optional[int]
    msg_id: str


# Maps error codes → category for dispatch routing
ERROR_CATEGORY_MAP = {
    "IBAN_INVALID_CHECKSUM": "account_identifier",
    "IBAN_WRONG_LENGTH": "account_identifier",
    "BIC_IBAN_COUNTRY_MISMATCH": "account_identifier",
    "BIC_INVALID_COUNTRY": "routing",
    "BENEFICIARY_NAME_INCOMPLETE": "beneficiary_data",
    "ADDRESS_INCOMPLETE": "beneficiary_data",
    "DUPLICATE_UETR": "duplicate",
    "XCHG_RATE_INCONSISTENT": "fx",
}

TECHNICAL_CATEGORIES = {"account_identifier", "routing", "duplicate", "fx"}
COMPLIANCE_CATEGORIES = {"beneficiary_data"}
