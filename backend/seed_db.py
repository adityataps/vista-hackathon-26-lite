import json

from db import get_db
from kb import seed_knowledge_base


if __name__ == "__main__":
    summary = seed_knowledge_base(get_db())
    print(f"seed_db: {json.dumps(summary)}")
