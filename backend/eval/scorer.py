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
    try:
        result.confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        result.errors.append(f"Could not parse confidence={confidence!r} as float")

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
