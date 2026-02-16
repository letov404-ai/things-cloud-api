"""Tests for Area2 entity support."""
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from things_cloud.api.account import Account, Credentials
from things_cloud.api.client import ThingsClient
from things_cloud.models.area import AreaItem
from things_cloud.models.todo import HistoryResponse
from things_cloud.utils import Util

FAKE_TIME = datetime(2024, 12, 9, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def mock_time(monkeypatch):
    monkeypatch.setattr("things_cloud.utils.Util.now", lambda: FAKE_TIME)


# --- AreaItem model ---

def test_area_creation():
    area = AreaItem(title="Health")
    assert area.title == "Health"
    assert len(area.uuid) == 22


def test_area_with_tags():
    area = AreaItem(title="Work", tags=["tag1-uuid-22chars00000"])
    assert area.tags == ["tag1-uuid-22chars00000"]


def test_area_to_update():
    area = AreaItem(title="Finance")
    update = area.to_update()
    payload = update.to_api_payload()
    uuid_key = list(payload.keys())[0]
    body = payload[uuid_key]
    assert body["e"] == "Area2"
    assert body["t"] == 0  # NEW
    assert body["p"]["tt"] == "Finance"


# --- ThingsClient area storage ---

@pytest.fixture()
def account_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def account(account_id: uuid.UUID, httpx_mock: HTTPXMock) -> Account:
    credentials = Credentials(
        email="test@example.com", password=SecretStr("pass123")
    )
    httpx_mock.add_response(
        200,
        json={
            "SLA-version-accepted": "5",
            "email": "test@example.com",
            "history-key": str(account_id),
            "issues": [],
            "maildrop-email": "test@things.email",
            "status": "SYAccountStatusActive",
        },
    )
    return Account.login(credentials)


@pytest.fixture()
def things(account: Account, httpx_mock: HTTPXMock) -> ThingsClient:
    httpx_mock.reset()
    httpx_mock.add_response(
        201,
        json={"headIndex": 0, "historyKeySessionSecret": "fake"},
    )
    return ThingsClient(account)


def test_areas_list(things: ThingsClient):
    assert things.areas() == []


def test_process_history_area_new(things: ThingsClient):
    """Processing Area2 NEW history creates AreaItem in _areas_store."""
    area_uuid = Util.uuid()
    history_data = {
        "items": [
            {
                area_uuid: {
                    "p": {
                        "tt": "Health",
                        "ix": 0,
                        "tg": [],
                    },
                    "e": "Area2",
                    "t": 0,
                }
            }
        ],
        "current-item-index": 1,
        "schema": 301,
        "start-total-content-size": 1,
        "end-total-content-size": 100,
        "latest-total-content-size": 100,
    }
    history = HistoryResponse.model_validate(history_data)
    things._process_history(history)

    areas = things.areas()
    assert len(areas) == 1
    assert areas[0].title == "Health"
    assert areas[0].uuid == area_uuid
