import os
import sys
import types
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bedrock_guard import BEDROCK_LIMIT_MESSAGE, BedrockDailyLimitExceeded, check_and_increment_bedrock_usage
from routers.exceptions import router as exceptions_router


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self._rows = self.conn.handle(sql, params)
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, usage_counts=None):
        self.autocommit = False
        self.usage_counts = list(usage_counts or [])
        self.updates = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        return None

    def rollback(self):
        return None

    def handle(self, sql, params):
        compact = " ".join(sql.split())
        if "INSERT INTO bedrock_usage" in compact:
            return [(self.usage_counts.pop(0),)]
        if "SELECT e.id, e.detected_errors" in compact:
            return [(
                7,
                [{"code": "IBAN_INVALID_CHECKSUM"}],
                1,
                "MSG-1",
                "UETR-1",
                "100.00",
                "USD",
                "DEUTDEFF",
                "BOFAUS3N",
                "DEUTDEFF",
                "BOFAUS3N",
                "Sender",
                "DE001",
                "Receiver",
                "US001",
                True,
                "<xml/>",
            )]
        if "INSERT INTO investigations" in compact and "RETURNING id" in compact:
            return [(11,)]
        if "UPDATE investigations" in compact or "UPDATE exceptions" in compact:
            self.updates.append((compact, params))
            return []
        raise AssertionError(f"Unexpected SQL in test: {compact}")


def test_bedrock_usage_counter_increments():
    conn = FakeConn(usage_counts=[1])
    with patch("bedrock_guard.get_db", return_value=conn), patch.dict(os.environ, {"BEDROCK_DAILY_LIMIT": "5"}):
        assert check_and_increment_bedrock_usage() == 1


def test_bedrock_usage_guard_raises_when_limit_exceeded():
    conn = FakeConn(usage_counts=[1, 2, 3])
    with patch("bedrock_guard.get_db", return_value=conn), patch.dict(os.environ, {"BEDROCK_DAILY_LIMIT": "2"}):
        assert check_and_increment_bedrock_usage() == 1
        assert check_and_increment_bedrock_usage() == 2
        with pytest.raises(BedrockDailyLimitExceeded):
            check_and_increment_bedrock_usage()


def test_investigate_route_streams_limit_message_instead_of_500():
    app = FastAPI()
    app.include_router(exceptions_router)

    class FakeGraph:
        def astream_events(self, *_args, **_kwargs):
            async def _gen():
                raise BedrockDailyLimitExceeded(call_count=101, daily_limit=100)
                yield None
            return _gen()

    conn = FakeConn()
    fake_main = types.SimpleNamespace(get_graph=lambda: FakeGraph())
    with (
        patch("routers.exceptions.get_db", return_value=conn),
        patch.dict(sys.modules, {"main": fake_main}),
        TestClient(app) as client,
    ):
        response = client.post("/api/exceptions/TX-00001/investigate")

    assert response.status_code == 200
    assert "BEDROCK_DAILY_LIMIT" in response.text
    assert '"type": "limit_reached"' in response.text
