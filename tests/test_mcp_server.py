"""Tests for Things Cloud MCP server tools."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from things_cloud.api.account import Account, Credentials
from things_cloud.api.client import ThingsClient
from things_cloud.models.area import AreaItem
from things_cloud.models.checklist import ChecklistItem
from things_cloud.models.tag import TagItem
from things_cloud.models.todo import Destination, Note, Status, TodoItem, Type
from things_cloud.utils import Util

import things_cloud.mcp_server as srv

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
def client(account: Account, httpx_mock: HTTPXMock) -> ThingsClient:
    httpx_mock.reset()
    httpx_mock.add_response(
        201,
        json={"headIndex": 0, "historyKeySessionSecret": "fake"},
    )
    return ThingsClient(account)


@pytest.fixture(autouse=True)
def inject_client(client: ThingsClient, monkeypatch):
    """Inject mock client and patch _sync_client to skip update() HTTP call."""
    srv._client = client
    monkeypatch.setattr(srv, "_sync_client", lambda: client)
    yield
    srv._client = None


def _tool(name: str):
    """Get the original function behind a FastMCP-wrapped tool."""
    wrapped = getattr(srv, name)
    # If FastMCP wraps it, .fn holds the original function
    if hasattr(wrapped, "fn"):
        return wrapped.fn
    return wrapped


def _make_task(title: str, **overrides) -> TodoItem:
    """Helper to create a TodoItem with specific state."""
    task = TodoItem(title=title)
    task._uuid = Util.uuid()
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


def _make_synced_task(title: str, **overrides) -> TodoItem:
    """Create a task that has a _synced_state (simulating previously committed)."""
    task = _make_task(title, **overrides)
    new = task._to_new()
    task._commit(new)
    return task


def _add_httpx_commit(httpx_mock: HTTPXMock, index: int = 1) -> None:
    """Add a commit response to httpx_mock."""
    httpx_mock.add_response(200, json={"server-head-index": index})


def _add_tag(client: ThingsClient, title: str, parent: str | None = None) -> TagItem:
    """Add a tag to the client's store."""
    tag = TagItem(title=title, parent=parent)
    client._tags[tag.uuid] = tag
    return tag


def _add_area(client: ThingsClient, title: str) -> AreaItem:
    """Add an area to the client's store."""
    area = AreaItem(title=title)
    client._areas_store[area.uuid] = area
    return area


def _add_checklist(
    client: ThingsClient, title: str, task_uuid: str, index: int = 0, status: int = 0
) -> ChecklistItem:
    """Add a checklist item to the client's store."""
    cl = ChecklistItem(title=title, task_uuid=task_uuid, index=index, status=status)
    client._checklist_items[cl.uuid] = cl
    return cl


# ===================================================================
# FORMAT HELPERS TESTS
# ===================================================================


class TestFormatTask:
    def test_basic(self, client: ThingsClient):
        task = _make_task("Buy milk")
        result = srv._format_task(task)
        assert "Buy milk" in result
        assert f"[uuid: {task.uuid}]" in result

    def test_with_project(self, client: ThingsClient):
        proj = _make_task("My Project", type=Type.PROJECT)
        client._items[proj.uuid] = proj
        task = _make_task("Task in proj", projects=[proj.uuid])
        client._items[task.uuid] = task
        result = srv._format_task(task, client)
        assert "[Project: My Project]" in result

    def test_with_due_date(self, client: ThingsClient):
        task = _make_task(
            "Deadline task",
            due_date=datetime(2026, 2, 20, tzinfo=UTC),
        )
        result = srv._format_task(task)
        assert "[Due: 2026-02-20]" in result

    def test_with_notes(self, client: ThingsClient):
        task = _make_task("Noted task")
        task.note = Note(v="Some important note")
        result = srv._format_task(task)
        assert "[Notes: Some important note]" in result


class TestFormatTaskList:
    def test_empty_list(self):
        result = srv._format_task_list([], "Inbox")
        assert "Inbox (0) -- no tasks." == result

    def test_with_tasks(self):
        t1 = _make_task("Task 1")
        t2 = _make_task("Task 2")
        result = srv._format_task_list([t1, t2], "Today")
        assert "Today (2):" in result
        assert "Task 1" in result
        assert "Task 2" in result


class TestFormatProject:
    def test_basic(self, client: ThingsClient):
        proj = _make_task("Work Project", type=Type.PROJECT)
        result = srv._format_project(proj)
        assert "Work Project" in result
        assert f"[uuid: {proj.uuid}]" in result


# ===================================================================
# HELPER TESTS
# ===================================================================


class TestParsePeriod:
    def test_days(self):
        assert srv._parse_period("3d") == 3

    def test_weeks(self):
        assert srv._parse_period("2w") == 14

    def test_months(self):
        assert srv._parse_period("1m") == 30

    def test_invalid(self):
        with pytest.raises(ValueError):
            srv._parse_period("abc")


class TestApplyWhen:
    def test_today(self):
        task = _make_task("Task")
        srv._apply_when(task, "today")
        assert task.destination == Destination.ANYTIME
        assert task.scheduled_date is not None

    def test_tomorrow(self):
        task = _make_task("Task")
        srv._apply_when(task, "tomorrow")
        assert task.destination == Destination.ANYTIME
        assert task.scheduled_date is not None

    def test_evening(self):
        task = _make_task("Task")
        srv._apply_when(task, "evening")
        assert task.destination == Destination.ANYTIME
        assert task._evening is True

    def test_someday(self):
        task = _make_task("Task")
        srv._apply_when(task, "someday")
        assert task.destination == Destination.SOMEDAY

    def test_date_string(self):
        task = _make_task("Task")
        srv._apply_when(task, "2026-03-15")
        assert task.destination == Destination.ANYTIME
        assert task.scheduled_date == datetime(2026, 3, 15, tzinfo=UTC)

    def test_invalid(self):
        task = _make_task("Task")
        with pytest.raises(ValueError, match="Invalid 'when' value"):
            srv._apply_when(task, "not-a-date")


# ===================================================================
# READ TOOLS TESTS
# ===================================================================


class TestGetInbox:
    def test_returns_inbox_tasks(self, client: ThingsClient):
        t = _make_task("Inbox task", destination=Destination.INBOX)
        client._items[t.uuid] = t
        result = _tool("get_inbox")()
        assert "Inbox (1):" in result
        assert "Inbox task" in result

    def test_empty(self, client: ThingsClient):
        result = _tool("get_inbox")()
        assert "no tasks" in result


class TestGetToday:
    def test_returns_today_tasks(self, client: ThingsClient):
        t = _make_task(
            "Today task",
            destination=Destination.ANYTIME,
            scheduled_date=FAKE_TODAY,
        )
        client._items[t.uuid] = t
        result = _tool("get_today")()
        assert "Today (1):" in result
        assert "Today task" in result


class TestGetUpcoming:
    def test_returns_future_tasks(self, client: ThingsClient):
        future = datetime(2025, 1, 15, tzinfo=UTC)
        t = _make_task(
            "Future task",
            destination=Destination.ANYTIME,
            scheduled_date=future,
        )
        client._items[t.uuid] = t
        result = _tool("get_upcoming")()
        assert "Upcoming (1):" in result
        assert "Future task" in result


class TestGetAnytime:
    def test_returns_anytime_tasks(self, client: ThingsClient):
        t = _make_task("Anytime task", destination=Destination.ANYTIME)
        client._items[t.uuid] = t
        result = _tool("get_anytime")()
        assert "Anytime (1):" in result
        assert "Anytime task" in result


class TestGetSomeday:
    def test_returns_someday_tasks(self, client: ThingsClient):
        t = _make_task("Someday task", destination=Destination.SOMEDAY)
        client._items[t.uuid] = t
        result = _tool("get_someday")()
        assert "Someday (1):" in result
        assert "Someday task" in result


class TestGetCompleted:
    def test_returns_completed_tasks(self, client: ThingsClient):
        t = _make_task(
            "Done task",
            status=Status.COMPLETE,
            completion_date=FAKE_TIME,
        )
        client._items[t.uuid] = t
        result = _tool("get_completed")("7d")
        assert "Completed" in result
        assert "Done task" in result


class TestGetTrash:
    def test_returns_trashed_tasks(self, client: ThingsClient):
        t = _make_task("Trashed task", trashed=True)
        client._items[t.uuid] = t
        result = _tool("get_trash")()
        assert "Trash (1):" in result
        assert "Trashed task" in result


class TestGetProjects:
    def test_returns_projects(self, client: ThingsClient):
        proj = _make_task("My Project", type=Type.PROJECT)
        client._items[proj.uuid] = proj
        result = _tool("get_projects")()
        assert "Projects (1):" in result
        assert "My Project" in result

    def test_include_tasks(self, client: ThingsClient):
        proj = _make_task("My Project", type=Type.PROJECT)
        task = _make_task("Proj task", projects=[proj.uuid])
        client._items[proj.uuid] = proj
        client._items[task.uuid] = task
        result = _tool("get_projects")(include_tasks=True)
        assert "Proj task" in result


class TestGetProjectTasks:
    def test_by_title(self, client: ThingsClient):
        proj = _make_task("Work", type=Type.PROJECT)
        task = _make_task("Do stuff", projects=[proj.uuid])
        client._items[proj.uuid] = proj
        client._items[task.uuid] = task
        result = _tool("get_project_tasks")(title="Work")
        assert "Do stuff" in result


class TestGetAreas:
    def test_returns_areas(self, client: ThingsClient):
        _add_area(client, "Personal")
        result = _tool("get_areas")()
        assert "Areas (1):" in result
        assert "Personal" in result


class TestGetTags:
    def test_returns_tags(self, client: ThingsClient):
        _add_tag(client, "urgent")
        result = _tool("get_tags")()
        assert "Tags (1):" in result
        assert "urgent" in result


class TestGetTaggedItems:
    def test_returns_tagged_tasks(self, client: ThingsClient):
        tag = _add_tag(client, "urgent")
        t = _make_task("Important", tags=[tag.uuid])
        client._items[t.uuid] = t
        result = _tool("get_tagged_items")("urgent")
        assert "Important" in result

    def test_tag_not_found(self, client: ThingsClient):
        result = _tool("get_tagged_items")("nonexistent")
        assert "not found" in result


class TestGetTask:
    def test_returns_task_details(self, client: ThingsClient):
        t = _make_task("Detailed task")
        t.note = Note(v="My notes here")
        client._items[t.uuid] = t
        result = _tool("get_task")(t.uuid)
        assert "Detailed task" in result
        assert "My notes here" in result
        assert "Status: TODO" in result

    def test_with_checklists(self, client: ThingsClient):
        t = _make_task("Task with checklist")
        client._items[t.uuid] = t
        _add_checklist(client, "Step 1", t.uuid, index=0)
        _add_checklist(client, "Step 2", t.uuid, index=1, status=3)
        result = _tool("get_task")(t.uuid)
        assert "[ ] Step 1" in result
        assert "[x] Step 2" in result

    def test_not_found(self, client: ThingsClient):
        result = _tool("get_task")("nonexistent00000000000")
        assert "No task found" in result


class TestGetChecklists:
    def test_returns_checklists(self, client: ThingsClient):
        t = _make_task("Checklist task")
        client._items[t.uuid] = t
        _add_checklist(client, "Item A", t.uuid, index=0)
        result = _tool("get_checklists")(task_uuid=t.uuid)
        assert "Item A" in result


# ===================================================================
# SEARCH TOOLS TESTS
# ===================================================================


class TestSearchTasks:
    def test_search_by_title(self, client: ThingsClient):
        t1 = _make_task("Buy groceries")
        t2 = _make_task("Clean house")
        client._items[t1.uuid] = t1
        client._items[t2.uuid] = t2
        result = _tool("search_tasks")("groceries")
        assert "Buy groceries" in result
        assert "Clean house" not in result

    def test_search_by_notes(self, client: ThingsClient):
        t = _make_task("Task")
        t.note = Note(v="important details here")
        client._items[t.uuid] = t
        result = _tool("search_tasks")("important")
        assert "Task" in result


class TestSearchAdvanced:
    def test_filter_by_status(self, client: ThingsClient):
        t1 = _make_task("Active task")
        t2 = _make_task("Done task", status=Status.COMPLETE)
        client._items[t1.uuid] = t1
        client._items[t2.uuid] = t2
        result = _tool("search_advanced")(status="completed")
        assert "Done task" in result
        assert "Active task" not in result

    def test_filter_by_tag(self, client: ThingsClient):
        tag = _add_tag(client, "work")
        t1 = _make_task("Work task", tags=[tag.uuid])
        t2 = _make_task("Personal task")
        client._items[t1.uuid] = t1
        client._items[t2.uuid] = t2
        result = _tool("search_advanced")(tag="work")
        assert "Work task" in result
        assert "Personal task" not in result


# ===================================================================
# CREATE TOOLS TESTS
# ===================================================================


class TestCreateTask:
    def test_basic(self, client: ThingsClient, httpx_mock: HTTPXMock):
        _add_httpx_commit(httpx_mock)
        result = _tool("create_task")("New task")
        assert "Created task" in result
        assert "New task" in result

    def test_with_notes(self, client: ThingsClient, httpx_mock: HTTPXMock):
        _add_httpx_commit(httpx_mock)
        result = _tool("create_task")("Noted task", notes="Some notes")
        assert "Created task" in result
        assert "Noted task" in result

    def test_with_when(self, client: ThingsClient, httpx_mock: HTTPXMock):
        _add_httpx_commit(httpx_mock)
        result = _tool("create_task")("Today task", when="today")
        assert "Created task" in result

    def test_with_checklist(self, client: ThingsClient, httpx_mock: HTTPXMock):
        # One commit for the task, two for checklist items
        _add_httpx_commit(httpx_mock, 1)
        _add_httpx_commit(httpx_mock, 2)
        _add_httpx_commit(httpx_mock, 3)
        result = _tool("create_task")("CL task", checklist_items=["Step 1", "Step 2"])
        assert "Created task" in result
        assert len(client._checklist_items) == 2


class TestCreateProject:
    def test_basic(self, client: ThingsClient, httpx_mock: HTTPXMock):
        _add_httpx_commit(httpx_mock)
        result = _tool("create_project")("New Project")
        assert "Created project" in result
        assert "New Project" in result

    def test_with_todos(self, client: ThingsClient, httpx_mock: HTTPXMock):
        # One commit for project, two for todos
        _add_httpx_commit(httpx_mock, 1)
        _add_httpx_commit(httpx_mock, 2)
        _add_httpx_commit(httpx_mock, 3)
        result = _tool("create_project")("Project", todos=["Task A", "Task B"])
        assert "Created project" in result
        # Check tasks were created
        tasks_in_project = [
            t for t in client._items.values() if t.type == Type.TASK
        ]
        assert len(tasks_in_project) == 2


class TestCreateTag:
    def test_basic(self, client: ThingsClient, httpx_mock: HTTPXMock):
        _add_httpx_commit(httpx_mock)
        result = _tool("create_tag")("important")
        assert "Created tag" in result
        assert "important" in result
        assert len(client._tags) == 1


class TestCreateArea:
    def test_basic(self, client: ThingsClient, httpx_mock: HTTPXMock):
        _add_httpx_commit(httpx_mock)
        result = _tool("create_area")("Work")
        assert "Created area" in result
        assert "Work" in result
        assert len(client._areas_store) == 1


# ===================================================================
# UPDATE TOOLS TESTS
# ===================================================================


class TestUpdateTask:
    def test_update_title(self, client: ThingsClient, httpx_mock: HTTPXMock):
        task = _make_synced_task("Old title")
        client._items[task.uuid] = task
        _add_httpx_commit(httpx_mock)
        result = _tool("update_task")(uuid=task.uuid, new_title="New title")
        assert "Updated task" in result
        assert "New title" in result

    def test_update_notes(self, client: ThingsClient, httpx_mock: HTTPXMock):
        task = _make_synced_task("Task")
        client._items[task.uuid] = task
        _add_httpx_commit(httpx_mock)
        result = _tool("update_task")(uuid=task.uuid, notes="New notes")
        assert "Updated task" in result

    def test_not_found(self, client: ThingsClient):
        result = _tool("update_task")(title="Nonexistent")
        assert "No tasks found" in result


class TestUpdateProject:
    def test_update_title(self, client: ThingsClient, httpx_mock: HTTPXMock):
        proj = _make_synced_task("Old Project", type=Type.PROJECT)
        client._items[proj.uuid] = proj
        _add_httpx_commit(httpx_mock)
        result = _tool("update_project")(uuid=proj.uuid, new_title="New Project")
        assert "Updated project" in result
        assert "New Project" in result


class TestRescheduleTask:
    def test_reschedule_to_today(self, client: ThingsClient, httpx_mock: HTTPXMock):
        task = _make_synced_task("My task")
        client._items[task.uuid] = task
        _add_httpx_commit(httpx_mock)
        result = _tool("reschedule_task")(uuid=task.uuid, when="today")
        assert "Rescheduled task" in result


# ===================================================================
# LIFECYCLE TOOLS TESTS
# ===================================================================


class TestCompleteTask:
    def test_complete(self, client: ThingsClient, httpx_mock: HTTPXMock):
        task = _make_synced_task("Finish this")
        client._items[task.uuid] = task
        _add_httpx_commit(httpx_mock)
        result = _tool("complete_task")(uuid=task.uuid)
        assert "Completed task" in result
        assert task.status == Status.COMPLETE


class TestCompleteProject:
    def test_complete(self, client: ThingsClient, httpx_mock: HTTPXMock):
        proj = _make_synced_task("Done Project", type=Type.PROJECT)
        client._items[proj.uuid] = proj
        _add_httpx_commit(httpx_mock)
        result = _tool("complete_project")(uuid=proj.uuid)
        assert "Completed project" in result
        assert proj.status == Status.COMPLETE


class TestCancelTask:
    def test_cancel(self, client: ThingsClient, httpx_mock: HTTPXMock):
        task = _make_synced_task("Cancel this")
        client._items[task.uuid] = task
        _add_httpx_commit(httpx_mock)
        result = _tool("cancel_task")(uuid=task.uuid)
        assert "Cancelled task" in result
        assert task.status == Status.CANCELLED


class TestCancelProject:
    def test_cancel(self, client: ThingsClient, httpx_mock: HTTPXMock):
        proj = _make_synced_task("Cancel Project", type=Type.PROJECT)
        client._items[proj.uuid] = proj
        _add_httpx_commit(httpx_mock)
        result = _tool("cancel_project")(uuid=proj.uuid)
        assert "Cancelled project" in result
        assert proj.status == Status.CANCELLED


class TestDeleteTask:
    def test_delete(self, client: ThingsClient, httpx_mock: HTTPXMock):
        task = _make_synced_task("Delete me")
        client._items[task.uuid] = task
        _add_httpx_commit(httpx_mock)
        result = _tool("delete_task")(uuid=task.uuid)
        assert "Deleted task" in result
        assert task.trashed is True


# ===================================================================
# CHECKLIST TOOL TESTS
# ===================================================================


class TestAddChecklistItem:
    def test_add_item(self, client: ThingsClient, httpx_mock: HTTPXMock):
        task = _make_task("My task")
        client._items[task.uuid] = task
        _add_httpx_commit(httpx_mock)
        result = _tool("add_checklist_item")(task_uuid=task.uuid, item_title="New item")
        assert "Added checklist item" in result
        assert "New item" in result
        assert len(client._checklist_items) == 1

    def test_add_by_title(self, client: ThingsClient, httpx_mock: HTTPXMock):
        task = _make_task("Unique task name")
        client._items[task.uuid] = task
        _add_httpx_commit(httpx_mock)
        result = _tool("add_checklist_item")(task_title="Unique task name", item_title="Step")
        assert "Added checklist item" in result
        assert "Step" in result
