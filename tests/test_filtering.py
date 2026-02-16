"""Tests for ThingsClient filtering methods."""
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from things_cloud.api.account import Account, Credentials
from things_cloud.api.client import ThingsClient
from things_cloud.models.todo import Destination, Status, TodoItem, Type
from things_cloud.utils import Util

FAKE_TIME = datetime(2024, 12, 9, 12, 0, 0, tzinfo=UTC)
FAKE_TODAY = datetime(2024, 12, 9, tzinfo=UTC)


@pytest.fixture(autouse=True)
def mock_time(monkeypatch):
    monkeypatch.setattr("things_cloud.utils.Util.now", lambda: FAKE_TIME)
    monkeypatch.setattr("things_cloud.utils.Util.today", lambda: FAKE_TODAY)


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


def _make_task(title: str, **overrides) -> TodoItem:
    """Helper to create a TodoItem with specific state."""
    task = TodoItem(title=title)
    task._uuid = Util.uuid()  # unique UUID
    for key, value in overrides.items():
        if key == "status":
            task._status = value
        elif key == "destination":
            task._destination = value
        elif key == "type":
            task._type = value
        elif key == "projects":
            task._projects = value
        elif key == "areas":
            task._areas = value
        else:
            setattr(task, key, value)
    return task


# --- inbox ---

def test_inbox_returns_inbox_tasks(things: ThingsClient):
    t1 = _make_task("Inbox task", destination=Destination.INBOX)
    t2 = _make_task("Anytime task", destination=Destination.ANYTIME)
    things._items[t1.uuid] = t1
    things._items[t2.uuid] = t2

    result = things.inbox()
    assert len(result) == 1
    assert result[0].title == "Inbox task"


def test_inbox_excludes_trashed(things: ThingsClient):
    t = _make_task("Trashed inbox", destination=Destination.INBOX, trashed=True)
    things._items[t.uuid] = t

    assert things.inbox() == []


def test_inbox_excludes_completed(things: ThingsClient):
    t = _make_task("Done inbox", destination=Destination.INBOX, status=Status.COMPLETE)
    things._items[t.uuid] = t

    assert things.inbox() == []


# --- today ---

def test_today_returns_scheduled_today(things: ThingsClient):
    t = _make_task("Today task", destination=Destination.ANYTIME, scheduled_date=FAKE_TODAY)
    things._items[t.uuid] = t

    result = things.today()
    assert len(result) == 1
    assert result[0].title == "Today task"


def test_today_excludes_trashed(things: ThingsClient):
    t = _make_task(
        "Trashed today", destination=Destination.ANYTIME,
        scheduled_date=FAKE_TODAY, trashed=True,
    )
    things._items[t.uuid] = t

    assert things.today() == []


def test_today_excludes_other_dates(things: ThingsClient):
    other_date = datetime(2024, 12, 10, tzinfo=UTC)
    t = _make_task("Tomorrow", destination=Destination.ANYTIME, scheduled_date=other_date)
    things._items[t.uuid] = t

    assert things.today() == []


# --- anytime ---

def test_anytime_returns_anytime_tasks(things: ThingsClient):
    t = _make_task("Anytime task", destination=Destination.ANYTIME)
    things._items[t.uuid] = t

    result = things.anytime()
    assert len(result) == 1
    assert result[0].title == "Anytime task"


def test_anytime_excludes_today_scheduled(things: ThingsClient):
    t = _make_task("Today task", destination=Destination.ANYTIME, scheduled_date=FAKE_TODAY)
    things._items[t.uuid] = t

    assert things.anytime() == []


# --- someday ---

def test_someday_returns_someday_tasks(things: ThingsClient):
    t = _make_task("Someday task", destination=Destination.SOMEDAY)
    things._items[t.uuid] = t

    result = things.someday()
    assert len(result) == 1
    assert result[0].title == "Someday task"


def test_someday_excludes_trashed(things: ThingsClient):
    t = _make_task("Trashed someday", destination=Destination.SOMEDAY, trashed=True)
    things._items[t.uuid] = t

    assert things.someday() == []


# --- projects ---

def test_projects_returns_project_items(things: ThingsClient):
    p = _make_task("My Project", type=Type.PROJECT)
    t = _make_task("Regular task")
    things._items[p.uuid] = p
    things._items[t.uuid] = t

    result = things.projects()
    assert len(result) == 1
    assert result[0].title == "My Project"
    assert result[0].type == Type.PROJECT


def test_projects_excludes_trashed(things: ThingsClient):
    p = _make_task("Trashed project", type=Type.PROJECT, trashed=True)
    things._items[p.uuid] = p

    assert things.projects() == []


# --- by_project ---

def test_by_project_filters_by_uuid(things: ThingsClient):
    proj = _make_task("Project", type=Type.PROJECT)
    t1 = _make_task("In project", projects=[proj.uuid])
    t2 = _make_task("Not in project")
    things._items[proj.uuid] = proj
    things._items[t1.uuid] = t1
    things._items[t2.uuid] = t2

    result = things.by_project(proj.uuid)
    assert len(result) == 1
    assert result[0].title == "In project"


# --- completed ---

def test_completed_returns_done_tasks(things: ThingsClient):
    t = _make_task("Done task", status=Status.COMPLETE)
    things._items[t.uuid] = t

    result = things.completed()
    assert len(result) == 1
    assert result[0].title == "Done task"


def test_completed_excludes_trashed(things: ThingsClient):
    t = _make_task("Trashed done", status=Status.COMPLETE, trashed=True)
    things._items[t.uuid] = t

    assert things.completed() == []


# --- trashed ---

def test_trashed_returns_trashed_tasks(things: ThingsClient):
    t = _make_task("Trashed task", trashed=True)
    things._items[t.uuid] = t

    result = things.trashed()
    assert len(result) == 1
    assert result[0].title == "Trashed task"


def test_trashed_excludes_non_trashed(things: ThingsClient):
    t = _make_task("Normal task")
    things._items[t.uuid] = t

    assert things.trashed() == []


# --- get ---

def test_get_returns_item(things: ThingsClient):
    t = _make_task("Find me")
    things._items[t.uuid] = t

    result = things.get(t.uuid)
    assert result is not None
    assert result.title == "Find me"


def test_get_returns_none_for_missing(things: ThingsClient):
    assert things.get("nonexistent00000000000") is None


# --- all_tasks ---

def test_all_tasks_returns_non_trashed_tasks(things: ThingsClient):
    t1 = _make_task("Active task")
    t2 = _make_task("Trashed", trashed=True)
    p = _make_task("Project", type=Type.PROJECT)
    things._items[t1.uuid] = t1
    things._items[t2.uuid] = t2
    things._items[p.uuid] = p

    result = things.all_tasks()
    assert len(result) == 1
    assert result[0].title == "Active task"
