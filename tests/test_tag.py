"""Tests for Tag3 entity support."""
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from things_cloud.api.account import Account, Credentials
from things_cloud.api.client import ThingsClient
from things_cloud.models.tag import TagItem
from things_cloud.models.todo import EntityType, HistoryResponse, NewBody
from things_cloud.utils import Util

FAKE_TIME = datetime(2024, 12, 9, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def mock_time(monkeypatch):
    monkeypatch.setattr("things_cloud.utils.Util.now", lambda: FAKE_TIME)


# --- TagItem model ---

def test_tag_creation():
    tag = TagItem(title="Work")
    assert tag.title == "Work"
    assert len(tag.uuid) == 22


def test_tag_with_parent():
    tag = TagItem(title="SubTag", parent="parent-uuid-goes-here22")
    assert tag.parent == "parent-uuid-goes-here22"


def test_tag_with_short_name():
    tag = TagItem(title="Important", short_name="imp")
    assert tag.short_name == "imp"


def test_tag_to_update():
    tag = TagItem(title="Work")
    update = tag.to_update()
    payload = update.to_api_payload()
    uuid_key = list(payload.keys())[0]
    body = payload[uuid_key]
    assert body["e"] == "Tag3"
    assert body["t"] == 0  # NEW
    assert body["p"]["tt"] == "Work"


def test_tag_to_update_with_parent():
    tag = TagItem(title="SubTag", parent="parent-uuid-goes-here22")
    update = tag.to_update()
    payload = update.to_api_payload()
    uuid_key = list(payload.keys())[0]
    assert payload[uuid_key]["p"]["pn"] == "parent-uuid-goes-here22"


# --- ThingsClient tag storage ---

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


def test_tags_list(things: ThingsClient):
    """tags() returns stored TagItems."""
    assert things.tags() == []


def test_process_history_tag_new(things: ThingsClient):
    """Processing Tag3 NEW history creates TagItem in _tags."""
    tag_uuid = Util.uuid()
    history_data = {
        "items": [
            {
                tag_uuid: {
                    "p": {
                        "tt": "Work",
                        "pn": None,
                        "sn": "",
                        "ix": 0,
                    },
                    "e": "Tag3",
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

    tags = things.tags()
    assert len(tags) == 1
    assert tags[0].title == "Work"
    assert tags[0].uuid == tag_uuid
