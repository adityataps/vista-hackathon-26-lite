import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.tools.knowledge_base_tool import search_knowledge_base
from kb import chunk_markdown


def test_chunk_markdown_groups_paragraphs():
    text = "One paragraph.\n\nTwo paragraph.\n\nThree paragraph."
    chunks = chunk_markdown(text, max_chars=40)
    assert len(chunks) >= 2
    assert all(chunk.strip() for chunk in chunks)


def test_search_knowledge_base_returns_pgvector_results():
    fake_results = [{"content": "IBANs must pass mod-97.", "score": 0.98, "source": "iban-format-registry.md"}]
    with patch("agents.tools.knowledge_base_tool.search_chunks", return_value=fake_results):
        payload = json.loads(search_knowledge_base.invoke({"query": "How is IBAN validity checked?"}))
    assert payload["results"] == fake_results


def test_search_knowledge_base_surfaces_errors():
    with patch("agents.tools.knowledge_base_tool.search_chunks", side_effect=RuntimeError("boom")):
        payload = json.loads(search_knowledge_base.invoke({"query": "sanctions"}))
    assert payload["results"] == []
    assert payload["error"] == "boom"
