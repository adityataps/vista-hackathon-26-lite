import json

from bedrock_guard import BedrockDailyLimitExceeded, BEDROCK_LIMIT_MESSAGE
from db import get_db
from kb import seed_knowledge_base


if __name__ == "__main__":
    try:
        summary = seed_knowledge_base(get_db())
    except BedrockDailyLimitExceeded as exc:
        summary = {
            "seeded_docs": 0,
            "skipped_docs": 0,
            "docs": [],
            "status": "bedrock-limit-reached",
            "message": BEDROCK_LIMIT_MESSAGE,
            "call_count": exc.call_count,
        }
    print(f"seed_db: {json.dumps(summary)}")
