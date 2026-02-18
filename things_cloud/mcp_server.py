"""Things Cloud MCP server with 29 tools for managing tasks, projects, tags, areas, and checklists."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, UTC

from fastmcp import FastMCP

from things_cloud.api.account import Account, Credentials
from things_cloud.api.client import ThingsClient
from things_cloud.models.area import AreaItem
from things_cloud.models.checklist import ChecklistItem
from things_cloud.models.tag import TagItem
from things_cloud.models.todo import Destination, Note, Status, TodoItem, Type
from things_cloud.utils import Util

mcp = FastMCP("Things Cloud")

_client: ThingsClient | None = None


def _get_client() -> ThingsClient:
    """Lazy-init client from THINGS_EMAIL + THINGS_PASSWORD env vars."""
    global _client
    if _client is None:
        email = os.environ.get("THINGS_EMAIL")
        password = os.environ.get("THINGS_PASSWORD")
        if not email or not password:
            raise RuntimeError(
                "THINGS_EMAIL and THINGS_PASSWORD environment variables must be set"
            )
        from pydantic import SecretStr

        credentials = Credentials(email=email, password=SecretStr(password))
        account = Account.login(credentials)
        _client = ThingsClient(account)
    return _client


def _sync_client() -> ThingsClient:
    """Get client and sync latest state from server."""
    client = _get_client()
    client.update()
    return client


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def _format_task(task: TodoItem, client: ThingsClient | None = None) -> str:
    """Format a single task as a human-readable line."""
    parts = [f"- {task.title} [uuid: {task.uuid}]"]

    # Project name
    if task.project and client:
        proj = client.get(task.project)
        if proj:
            parts.append(f"[Project: {proj.title}]")

    # Tags
    if task.tags and client:
        tag_names = []
        for tag_uuid in task.tags:
            for t in client.tags():
                if t.uuid == tag_uuid:
                    tag_names.append(t.title)
                    break
        if tag_names:
            parts.append(f"[Tags: {', '.join(tag_names)}]")

    # Due date
    if task.due_date:
        parts.append(f"[Due: {task.due_date.strftime('%Y-%m-%d')}]")

    # Scheduled date
    if task.scheduled_date:
        parts.append(f"[Scheduled: {task.scheduled_date.strftime('%Y-%m-%d')}]")

    # Notes preview
    if task.note and task.note.value:
        preview = task.note.value[:80]
        if len(task.note.value) > 80:
            preview += "..."
        parts.append(f"[Notes: {preview}]")

    return " ".join(parts)


def _format_task_list(
    tasks: list[TodoItem], title: str, client: ThingsClient | None = None
) -> str:
    """Format a list of tasks with a header."""
    if not tasks:
        return f"{title} (0) -- no tasks."
    lines = [f"{title} ({len(tasks)}):"]
    for task in tasks:
        lines.append(_format_task(task, client))
    return "\n".join(lines)


def _format_project(project: TodoItem, client: ThingsClient | None = None) -> str:
    """Format a project item as a human-readable line."""
    parts = [f"- {project.title} [uuid: {project.uuid}]"]

    # Area
    if project.area and client:
        for a in client.areas():
            if a.uuid == project.area:
                parts.append(f"[Area: {a.title}]")
                break

    # Tags
    if project.tags and client:
        tag_names = []
        for tag_uuid in project.tags:
            for t in client.tags():
                if t.uuid == tag_uuid:
                    tag_names.append(t.title)
                    break
        if tag_names:
            parts.append(f"[Tags: {', '.join(tag_names)}]")

    # Due date
    if project.due_date:
        parts.append(f"[Due: {project.due_date.strftime('%Y-%m-%d')}]")

    # Notes preview
    if project.note and project.note.value:
        preview = project.note.value[:80]
        if len(project.note.value) > 80:
            preview += "..."
        parts.append(f"[Notes: {preview}]")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Title-based lookup helpers
# ---------------------------------------------------------------------------


def _find_task_by_title(
    title: str, scope: list[TodoItem], label: str = "tasks"
) -> TodoItem:
    """Find a task by title: exact match first, then substring. Error if 0 or >1 matches."""
    # Exact match
    exact = [t for t in scope if t.title == title]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        names = ", ".join(f'"{t.title}" [{t.uuid}]' for t in exact)
        raise ValueError(
            f"Multiple {label} match '{title}': {names}. Use uuid instead."
        )

    # Substring match
    lower_title = title.lower()
    matches = [t for t in scope if lower_title in t.title.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        raise ValueError(f"No {label} found matching '{title}'.")
    names = ", ".join(f'"{t.title}" [{t.uuid}]' for t in matches)
    raise ValueError(
        f"Multiple {label} match '{title}': {names}. Use uuid instead."
    )


def _resolve_task(
    title: str | None, uuid: str | None, client: ThingsClient
) -> TodoItem:
    """Resolve a task by uuid or title."""
    if uuid:
        task = client.get(uuid)
        if not task:
            raise ValueError(f"No task found with uuid '{uuid}'.")
        return task
    if title:
        return _find_task_by_title(title, client.all_tasks())
    raise ValueError("Either title or uuid must be provided.")


def _resolve_project(
    title: str | None, uuid: str | None, client: ThingsClient
) -> TodoItem:
    """Resolve a project by uuid or title."""
    if uuid:
        proj = client.get(uuid)
        if not proj or proj.type != Type.PROJECT:
            raise ValueError(f"No project found with uuid '{uuid}'.")
        return proj
    if title:
        return _find_project_by_title(title, client)
    raise ValueError("Either title or uuid must be provided.")


def _find_project_by_title(title: str, client: ThingsClient) -> TodoItem:
    """Find a project by title."""
    return _find_task_by_title(title, client.projects(), label="projects")


# ---------------------------------------------------------------------------
# When helper
# ---------------------------------------------------------------------------


def _apply_when(task: TodoItem, when: str) -> None:
    """Apply a 'when' scheduling value to a task.

    Handles: today, tomorrow, evening, someday, inbox, anytime, YYYY-MM-DD
    """
    when_lower = when.lower().strip()
    if when_lower == "today":
        task.today()
    elif when_lower == "tomorrow":
        tomorrow = datetime.combine(
            date.today() + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        )
        task._destination = Destination.ANYTIME
        task.scheduled_date = tomorrow
    elif when_lower == "evening":
        task.evening()
    elif when_lower == "someday":
        task._destination = Destination.SOMEDAY
        task.scheduled_date = None
    elif when_lower == "inbox":
        task._destination = Destination.INBOX
        task.scheduled_date = None
    elif when_lower == "anytime":
        task._destination = Destination.ANYTIME
        task.scheduled_date = None
    else:
        # Try YYYY-MM-DD
        try:
            dt = datetime.strptime(when_lower, "%Y-%m-%d").replace(tzinfo=UTC)
            task._destination = Destination.ANYTIME
            task.scheduled_date = dt
        except ValueError:
            raise ValueError(
                f"Invalid 'when' value: '{when}'. "
                "Use: today, tomorrow, evening, someday, inbox, anytime, or YYYY-MM-DD."
            )


# ---------------------------------------------------------------------------
# Tag / Area helpers
# ---------------------------------------------------------------------------


def _apply_tags(item: TodoItem, tag_names: list[str], client: ThingsClient) -> None:
    """Resolve tag names to UUIDs and set on item."""
    all_tags = client.tags()
    tag_map = {t.title.lower(): t.uuid for t in all_tags}
    uuids = []
    for name in tag_names:
        uid = tag_map.get(name.lower())
        if uid is None:
            raise ValueError(f"Tag '{name}' not found.")
        uuids.append(uid)
    item.tags = uuids


def _find_area_uuid(name: str, client: ThingsClient) -> str:
    """Find area UUID by name."""
    for a in client.areas():
        if a.title.lower() == name.lower():
            return a.uuid
    raise ValueError(f"Area '{name}' not found.")


def _parse_period(period: str) -> int:
    """Parse period string like '3d', '1w', '1m' to number of days."""
    m = re.match(r"^(\d+)([dwm])$", period.lower().strip())
    if not m:
        raise ValueError(
            f"Invalid period '{period}'. Use format like '3d', '1w', '1m'."
        )
    value = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return value
    elif unit == "w":
        return value * 7
    elif unit == "m":
        return value * 30
    return value


# ===================================================================
# READ TOOLS (14)
# ===================================================================


@mcp.tool()
def get_inbox() -> str:
    """Get all tasks in the Inbox."""
    client = _sync_client()
    tasks = client.inbox()
    return _format_task_list(tasks, "Inbox", client)


@mcp.tool()
def get_today() -> str:
    """Get all tasks scheduled for today."""
    client = _sync_client()
    tasks = client.today()
    return _format_task_list(tasks, "Today", client)


@mcp.tool()
def get_upcoming() -> str:
    """Get tasks scheduled for the future (after today)."""
    client = _sync_client()
    now = Util.today().date()
    tasks = [
        t
        for t in client._active_tasks()
        if t.scheduled_date is not None
        and t.scheduled_date.date() > now
        and not bool(t.recurrence_rule)
    ]
    return _format_task_list(tasks, "Upcoming", client)


@mcp.tool()
def get_anytime() -> str:
    """Get all tasks in the Anytime list."""
    client = _sync_client()
    tasks = client.anytime()
    return _format_task_list(tasks, "Anytime", client)


@mcp.tool()
def get_someday() -> str:
    """Get all tasks in the Someday list."""
    client = _sync_client()
    tasks = client.someday()
    return _format_task_list(tasks, "Someday", client)


@mcp.tool()
def get_completed(period: str = "7d") -> str:
    """Get completed tasks, optionally filtered by period (e.g. '7d', '2w', '1m')."""
    client = _sync_client()
    days = _parse_period(period)
    cutoff = Util.now() - timedelta(days=days)
    tasks = [
        t
        for t in client.completed()
        if t.completion_date and t.completion_date >= cutoff
    ]
    return _format_task_list(tasks, f"Completed (last {period})", client)


@mcp.tool()
def get_trash() -> str:
    """Get all trashed tasks."""
    client = _sync_client()
    tasks = client.trashed()
    return _format_task_list(tasks, "Trash", client)


@mcp.tool()
def get_projects(include_tasks: bool = False) -> str:
    """Get all projects. If include_tasks=True, list tasks under each project."""
    client = _sync_client()
    projects = client.projects()
    if not projects:
        return "Projects (0) -- no projects."
    lines = [f"Projects ({len(projects)}):"]
    for proj in projects:
        lines.append(_format_project(proj, client))
        if include_tasks:
            tasks = client.by_project(proj.uuid)
            for t in tasks:
                lines.append(f"  {_format_task(t, client)}")
    return "\n".join(lines)


@mcp.tool()
def get_project_tasks(title: str | None = None, uuid: str | None = None) -> str:
    """Get all tasks belonging to a specific project (by title or uuid)."""
    client = _sync_client()
    proj = _resolve_project(title, uuid, client)
    tasks = client.by_project(proj.uuid)
    return _format_task_list(tasks, f"Project: {proj.title}", client)


@mcp.tool()
def get_areas(include_items: bool = False) -> str:
    """Get all areas. If include_items=True, list projects and tasks under each area."""
    client = _sync_client()
    areas = client.areas()
    if not areas:
        return "Areas (0) -- no areas."
    lines = [f"Areas ({len(areas)}):"]
    for area in areas:
        lines.append(f"- {area.title} [uuid: {area.uuid}]")
        if include_items:
            # Find projects and tasks in this area
            for item in client._items.values():
                if not item.trashed and item.area == area.uuid:
                    if item.type == Type.PROJECT:
                        lines.append(f"  {_format_project(item, client)}")
                    else:
                        lines.append(f"  {_format_task(item, client)}")
    return "\n".join(lines)


@mcp.tool()
def get_tags() -> str:
    """Get all tags."""
    client = _sync_client()
    tags = client.tags()
    if not tags:
        return "Tags (0) -- no tags."
    lines = [f"Tags ({len(tags)}):"]
    for tag in tags:
        lines.append(f"- {tag.title} [uuid: {tag.uuid}]")
    return "\n".join(lines)


@mcp.tool()
def get_tagged_items(tag_title: str) -> str:
    """Get all tasks with a specific tag (by tag title)."""
    client = _sync_client()
    # Find the tag UUID
    tag_uuid = None
    for t in client.tags():
        if t.title.lower() == tag_title.lower():
            tag_uuid = t.uuid
            break
    if tag_uuid is None:
        return f"Tag '{tag_title}' not found."

    tasks = [
        t
        for t in client.all_tasks()
        if tag_uuid in t.tags
    ]
    return _format_task_list(tasks, f"Tagged: {tag_title}", client)


@mcp.tool()
def get_task(uuid: str) -> str:
    """Get detailed information about a specific task by UUID, including checklists."""
    client = _sync_client()
    task = client.get(uuid)
    if not task:
        return f"No task found with uuid '{uuid}'."

    lines = [_format_task(task, client)]

    # Status info
    lines.append(f"  Status: {task.status.name}")
    lines.append(f"  Type: {task.type.name}")
    lines.append(f"  Destination: {task.destination.name}")

    if task.is_today:
        lines.append("  Today: Yes")
    if task.is_evening:
        lines.append("  Evening: Yes")
    if task.trashed:
        lines.append("  Trashed: Yes")

    # Full notes
    if task.note and task.note.value:
        lines.append(f"  Notes: {task.note.value}")

    # Checklists
    checklists = client.checklists_for(uuid)
    if checklists:
        lines.append(f"  Checklists ({len(checklists)}):")
        for cl in checklists:
            check = "[x]" if cl.status == 3 else "[ ]"
            lines.append(f"    {check} {cl.title}")

    return "\n".join(lines)


@mcp.tool()
def get_checklists(task_title: str | None = None, task_uuid: str | None = None) -> str:
    """Get checklist items for a specific task."""
    client = _sync_client()
    task = _resolve_task(task_title, task_uuid, client)
    checklists = client.checklists_for(task.uuid)
    if not checklists:
        return f"No checklists for '{task.title}'."
    lines = [f"Checklists for '{task.title}' ({len(checklists)}):"]
    for cl in checklists:
        check = "[x]" if cl.status == 3 else "[ ]"
        lines.append(f"  {check} {cl.title} [uuid: {cl.uuid}]")
    return "\n".join(lines)


# ===================================================================
# SEARCH TOOLS (2)
# ===================================================================


@mcp.tool()
def search_tasks(query: str) -> str:
    """Search tasks by substring in title or notes."""
    client = _sync_client()
    q = query.lower()
    results = [
        t
        for t in client.all_tasks()
        if q in t.title.lower() or (t.note and q in t.note.value.lower())
    ]
    return _format_task_list(results, f"Search: '{query}'", client)


@mcp.tool()
def search_advanced(
    status: str | None = None,
    deadline: str | None = None,
    tag: str | None = None,
    area: str | None = None,
    project: str | None = None,
    type: str | None = None,
) -> str:
    """Advanced search with filters: status (todo/completed/cancelled), deadline (YYYY-MM-DD), tag, area, project, type (task/project/heading)."""
    client = _sync_client()
    items: list[TodoItem] = [t for t in client._items.values() if not t.trashed]

    if status:
        status_map = {
            "todo": Status.TODO,
            "completed": Status.COMPLETE,
            "complete": Status.COMPLETE,
            "cancelled": Status.CANCELLED,
            "canceled": Status.CANCELLED,
        }
        s = status_map.get(status.lower())
        if s is not None:
            items = [t for t in items if t.status == s]

    if deadline:
        try:
            dl = datetime.strptime(deadline, "%Y-%m-%d").replace(tzinfo=UTC)
            items = [
                t
                for t in items
                if t.due_date and t.due_date.date() <= dl.date()
            ]
        except ValueError:
            return f"Invalid deadline format: '{deadline}'. Use YYYY-MM-DD."

    if tag:
        tag_uuid = None
        for tg in client.tags():
            if tg.title.lower() == tag.lower():
                tag_uuid = tg.uuid
                break
        if tag_uuid is None:
            return f"Tag '{tag}' not found."
        items = [t for t in items if tag_uuid in t.tags]

    if area:
        try:
            area_uuid = _find_area_uuid(area, client)
        except ValueError as e:
            return str(e)
        items = [t for t in items if t.area == area_uuid]

    if project:
        try:
            proj = _find_project_by_title(project, client)
        except ValueError as e:
            return str(e)
        items = [t for t in items if t.project == proj.uuid]

    if type:
        type_map = {
            "task": Type.TASK,
            "project": Type.PROJECT,
            "heading": Type.HEADING,
        }
        tp = type_map.get(type.lower())
        if tp is not None:
            items = [t for t in items if t.type == tp]

    return _format_task_list(items, "Advanced Search", client)


# ===================================================================
# CREATE TOOLS (4)
# ===================================================================


@mcp.tool()
def create_task(
    title: str,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    project: str | None = None,
    area: str | None = None,
    checklist_items: list[str] | None = None,
    heading: str | None = None,
) -> str:
    """Create a new task with optional notes, scheduling, deadline, tags, project, area, and checklist items."""
    client = _sync_client()
    task = TodoItem(title=title)

    if notes:
        task.note = Note(v=notes)

    if when:
        _apply_when(task, when)

    if deadline:
        try:
            task.due_date = datetime.strptime(deadline, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return f"Invalid deadline format: '{deadline}'. Use YYYY-MM-DD."

    if tags:
        try:
            _apply_tags(task, tags, client)
        except ValueError as e:
            return str(e)

    if project:
        try:
            proj = _find_project_by_title(project, client)
            task.project = proj
        except ValueError as e:
            return str(e)

    if area:
        try:
            area_uuid = _find_area_uuid(area, client)
            task.area = area_uuid
        except ValueError as e:
            return str(e)

    if heading:
        task.action_group = [heading]

    client.commit(task)
    client._items[task.uuid] = task

    # Create checklist items
    if checklist_items:
        for idx, item_title in enumerate(checklist_items):
            cl = ChecklistItem(title=item_title, task_uuid=task.uuid, index=idx)
            update = cl.to_update()
            client._ThingsClient__commit(update)
            client._checklist_items[cl.uuid] = cl

    return f"Created task: {_format_task(task, client)}"


@mcp.tool()
def create_project(
    title: str,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    area: str | None = None,
    todos: list[str] | None = None,
) -> str:
    """Create a new project with optional notes, scheduling, deadline, tags, area, and initial tasks."""
    client = _sync_client()
    task = TodoItem(title=title)
    task.as_project()

    if notes:
        task.note = Note(v=notes)

    if when:
        _apply_when(task, when)

    if deadline:
        try:
            task.due_date = datetime.strptime(deadline, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return f"Invalid deadline format: '{deadline}'. Use YYYY-MM-DD."

    if tags:
        try:
            _apply_tags(task, tags, client)
        except ValueError as e:
            return str(e)

    if area:
        try:
            area_uuid = _find_area_uuid(area, client)
            task.area = area_uuid
        except ValueError as e:
            return str(e)

    client.commit(task)
    client._items[task.uuid] = task

    # Create initial tasks
    if todos:
        for todo_title in todos:
            t = TodoItem(title=todo_title)
            t.project = task
            client.commit(t)
            client._items[t.uuid] = t

    return f"Created project: {_format_project(task, client)}"


@mcp.tool()
def create_tag(title: str, parent: str | None = None) -> str:
    """Create a new tag with optional parent tag name."""
    client = _sync_client()

    parent_uuid = None
    if parent:
        for t in client.tags():
            if t.title.lower() == parent.lower():
                parent_uuid = t.uuid
                break
        if parent_uuid is None:
            return f"Parent tag '{parent}' not found."

    tag = TagItem(title=title, parent=parent_uuid)
    update = tag.to_update()
    client._ThingsClient__commit(update)
    client._tags[tag.uuid] = tag
    return f"Created tag: {tag.title} [uuid: {tag.uuid}]"


@mcp.tool()
def create_area(title: str) -> str:
    """Create a new area."""
    client = _sync_client()
    area = AreaItem(title=title)
    update = area.to_update()
    client._ThingsClient__commit(update)
    client._areas_store[area.uuid] = area
    return f"Created area: {area.title} [uuid: {area.uuid}]"


# ===================================================================
# UPDATE TOOLS (3)
# ===================================================================


@mcp.tool()
def update_task(
    title: str | None = None,
    uuid: str | None = None,
    new_title: str | None = None,
    notes: str | None = None,
    append_notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    add_tags: list[str] | None = None,
    project: str | None = None,
    area: str | None = None,
    reminder: str | None = None,
) -> str:
    """Update an existing task (identified by title or uuid)."""
    client = _sync_client()
    try:
        task = _resolve_task(title, uuid, client)
    except ValueError as e:
        return str(e)

    if new_title:
        task.title = new_title

    if notes is not None:
        task.note = Note(v=notes)

    if append_notes:
        existing = task.note.value if task.note else ""
        task.note = Note(v=existing + append_notes)

    if when:
        try:
            _apply_when(task, when)
        except ValueError as e:
            return str(e)

    if deadline:
        try:
            task.due_date = datetime.strptime(deadline, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return f"Invalid deadline format: '{deadline}'. Use YYYY-MM-DD."

    if tags is not None:
        try:
            _apply_tags(task, tags, client)
        except ValueError as e:
            return str(e)

    if add_tags:
        try:
            all_tag_items = client.tags()
            tag_map = {t.title.lower(): t.uuid for t in all_tag_items}
            existing_tags = list(task.tags)
            for name in add_tags:
                uid = tag_map.get(name.lower())
                if uid is None:
                    return f"Tag '{name}' not found."
                if uid not in existing_tags:
                    existing_tags.append(uid)
            task.tags = existing_tags
        except ValueError as e:
            return str(e)

    if project is not None:
        try:
            proj = _find_project_by_title(project, client)
            task.project = proj
        except ValueError as e:
            return str(e)

    if area is not None:
        try:
            area_uuid = _find_area_uuid(area, client)
            task.area = area_uuid
        except ValueError as e:
            return str(e)

    if reminder:
        try:
            from datetime import time

            parts = reminder.split(":")
            task.reminder = time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return f"Invalid reminder format: '{reminder}'. Use HH:MM."

    client.commit(task)
    return f"Updated task: {_format_task(task, client)}"


@mcp.tool()
def update_project(
    title: str | None = None,
    uuid: str | None = None,
    new_title: str | None = None,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    add_tags: list[str] | None = None,
    area: str | None = None,
) -> str:
    """Update an existing project (identified by title or uuid)."""
    client = _sync_client()
    try:
        proj = _resolve_project(title, uuid, client)
    except ValueError as e:
        return str(e)

    if new_title:
        proj.title = new_title

    if notes is not None:
        proj.note = Note(v=notes)

    if when:
        try:
            _apply_when(proj, when)
        except ValueError as e:
            return str(e)

    if deadline:
        try:
            proj.due_date = datetime.strptime(deadline, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return f"Invalid deadline format: '{deadline}'. Use YYYY-MM-DD."

    if tags is not None:
        try:
            _apply_tags(proj, tags, client)
        except ValueError as e:
            return str(e)

    if add_tags:
        try:
            all_tag_items = client.tags()
            tag_map = {t.title.lower(): t.uuid for t in all_tag_items}
            existing_tags = list(proj.tags)
            for name in add_tags:
                uid = tag_map.get(name.lower())
                if uid is None:
                    return f"Tag '{name}' not found."
                if uid not in existing_tags:
                    existing_tags.append(uid)
            proj.tags = existing_tags
        except ValueError as e:
            return str(e)

    if area is not None:
        try:
            area_uuid = _find_area_uuid(area, client)
            proj.area = area_uuid
        except ValueError as e:
            return str(e)

    client.commit(proj)
    return f"Updated project: {_format_project(proj, client)}"


@mcp.tool()
def reschedule_task(
    title: str | None = None, uuid: str | None = None, when: str = "today"
) -> str:
    """Reschedule a task (identified by title or uuid) to a new date/time."""
    client = _sync_client()
    try:
        task = _resolve_task(title, uuid, client)
    except ValueError as e:
        return str(e)

    try:
        _apply_when(task, when)
    except ValueError as e:
        return str(e)

    client.commit(task)
    return f"Rescheduled task: {_format_task(task, client)}"


# ===================================================================
# LIFECYCLE TOOLS (5)
# ===================================================================


@mcp.tool()
def complete_task(title: str | None = None, uuid: str | None = None) -> str:
    """Mark a task as completed."""
    client = _sync_client()
    try:
        task = _resolve_task(title, uuid, client)
    except ValueError as e:
        return str(e)

    try:
        task.complete()
    except ValueError as e:
        return str(e)

    client.commit(task)
    return f"Completed task: {task.title} [{task.uuid}]"


@mcp.tool()
def complete_project(title: str | None = None, uuid: str | None = None) -> str:
    """Mark a project as completed."""
    client = _sync_client()
    try:
        proj = _resolve_project(title, uuid, client)
    except ValueError as e:
        return str(e)

    try:
        proj.complete()
    except ValueError as e:
        return str(e)

    client.commit(proj)
    return f"Completed project: {proj.title} [{proj.uuid}]"


@mcp.tool()
def cancel_task(title: str | None = None, uuid: str | None = None) -> str:
    """Cancel a task."""
    client = _sync_client()
    try:
        task = _resolve_task(title, uuid, client)
    except ValueError as e:
        return str(e)

    try:
        task.cancel()
    except ValueError as e:
        return str(e)

    client.commit(task)
    return f"Cancelled task: {task.title} [{task.uuid}]"


@mcp.tool()
def cancel_project(title: str | None = None, uuid: str | None = None) -> str:
    """Cancel a project."""
    client = _sync_client()
    try:
        proj = _resolve_project(title, uuid, client)
    except ValueError as e:
        return str(e)

    try:
        proj.cancel()
    except ValueError as e:
        return str(e)

    client.commit(proj)
    return f"Cancelled project: {proj.title} [{proj.uuid}]"


@mcp.tool()
def delete_task(title: str | None = None, uuid: str | None = None) -> str:
    """Move a task to the trash."""
    client = _sync_client()
    try:
        task = _resolve_task(title, uuid, client)
    except ValueError as e:
        return str(e)

    try:
        task.delete()
    except ValueError as e:
        return str(e)

    client.commit(task)
    return f"Deleted task: {task.title} [{task.uuid}]"


# ===================================================================
# CHECKLIST TOOL (1)
# ===================================================================


@mcp.tool()
def add_checklist_item(
    task_title: str | None = None,
    task_uuid: str | None = None,
    item_title: str = "",
) -> str:
    """Add a checklist item to a task."""
    client = _sync_client()
    try:
        task = _resolve_task(task_title, task_uuid, client)
    except ValueError as e:
        return str(e)

    # Determine next index
    existing = client.checklists_for(task.uuid)
    next_index = max((cl.index for cl in existing), default=-1) + 1

    cl = ChecklistItem(title=item_title, task_uuid=task.uuid, index=next_index)
    update = cl.to_update()
    client._ThingsClient__commit(update)
    client._checklist_items[cl.uuid] = cl
    return f"Added checklist item '{item_title}' to '{task.title}' [uuid: {cl.uuid}]"


# ===================================================================
# Entry point
# ===================================================================


def main():
    mcp.run()


if __name__ == "__main__":
    main()
