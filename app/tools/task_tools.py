from __future__ import annotations

import datetime as dt
import secrets
from typing import Optional

from googleapiclient.discovery import build
from pydantic import BaseModel, Field


RFC3339_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
CONFIRMATION_TTL_MINUTES = 10


class PendingTaskActionStore:
    def __init__(self) -> None:
        self._actions: dict[str, dict] = {}

    def create(self, *, action: str, task_id: str, task_title: str) -> str:
        token = secrets.token_urlsafe(16)
        self._actions[token] = {
            "action": action,
            "task_id": task_id,
            "task_title": task_title,
            "expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=CONFIRMATION_TTL_MINUTES),
        }
        return token

    def validate(self, *, token: str, action: str, task_id: str) -> tuple[bool, str | None]:
        payload = self._actions.get(token)
        if not payload:
            return False, "Invalid confirmation token."
        if payload["expires_at"] < dt.datetime.now(dt.timezone.utc):
            self._actions.pop(token, None)
            return False, "Confirmation token expired. Please ask again."
        if payload["action"] != action or payload["task_id"] != task_id:
            return False, "Confirmation token does not match this task action."
        self._actions.pop(token, None)
        return True, None


pending_task_actions = PendingTaskActionStore()


def _tasks_service(credentials):
    return build("tasks", "v1", credentials=credentials)


class CreateTaskInput(BaseModel):
    title: str = Field(..., description="The title of the task.")
    notes: str = Field(default="", description="Optional details or notes for the task.")
    due: Optional[str] = Field(
        default=None,
        description="Optional RFC3339 or ISO 8601 due date/time. Example: 2026-03-20T17:00:00Z.",
    )


class ListTasksInput(BaseModel):
    show_completed: bool = Field(default=False, description="Include completed tasks when true.")
    max_results: int = Field(default=20, ge=1, le=100, description="Maximum number of tasks to return.")


class UpdateTaskInput(BaseModel):
    task_id: str = Field(..., description="The Google Task ID to update.")
    title: Optional[str] = Field(default=None, description="New title for the task.")
    notes: Optional[str] = Field(default=None, description="New notes/details for the task.")
    due: Optional[str] = Field(
        default=None,
        description="New RFC3339/ISO 8601 due datetime. Provide null/empty to clear externally if supported by caller logic.",
    )
    status: Optional[str] = Field(
        default=None,
        description="Task status. Use 'needsAction' for open tasks or 'completed' for done tasks.",
    )


class DeleteTaskInput(BaseModel):
    task_id: str = Field(..., description="The Google Task ID to delete.")


class GetTaskInput(BaseModel):
    task_id: str = Field(..., description="The Google Task ID to fetch.")


class SetTaskDueDateInput(BaseModel):
    task_id: str = Field(..., description="The Google Task ID to update.")
    due: str = Field(..., description="RFC3339 or ISO 8601 due datetime.")


class CompleteTaskInput(BaseModel):
    task_id: str = Field(..., description="The Google Task ID to mark as completed.")


class ClearTaskDueDateInput(BaseModel):
    task_id: str = Field(..., description="The Google Task ID whose due date should be removed.")


class FindTaskInput(BaseModel):
    query: str = Field(..., description="Natural language task reference, such as a title or keyword.")
    include_completed: bool = Field(default=True, description="Search completed tasks too when true.")


class UpdateTaskByReferenceInput(BaseModel):
    task_reference: str = Field(..., description="Natural language task title or keyword.")
    title: Optional[str] = None
    notes: Optional[str] = None
    due: Optional[str] = None
    status: Optional[str] = None


class DueDateByReferenceInput(BaseModel):
    task_reference: str = Field(..., description="Natural language task title or keyword.")
    due: str = Field(..., description="RFC3339 or ISO 8601 due datetime.")


class DestructiveTaskByReferenceInput(BaseModel):
    task_reference: str = Field(..., description="Natural language task title or keyword.")
    confirmation_token: Optional[str] = Field(
        default=None,
        description="Confirmation token from a previous response. Required to actually delete or clear a deadline.",
    )


class CompleteTaskByReferenceInput(BaseModel):
    task_reference: str = Field(..., description="Natural language task title or keyword.")


class GetTaskByReferenceInput(BaseModel):
    task_reference: str = Field(..., description="Natural language task title or keyword.")



def _normalize_due(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        return cleaned
    parsed = dt.datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).strftime(RFC3339_FORMAT)



def _format_due_for_voice(due: str | None) -> str:
    if not due:
        return "No deadline"
    try:
        parsed = dt.datetime.fromisoformat(due.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return due



def _format_task(task: dict) -> str:
    title = task.get("title", "Untitled")
    task_id = task.get("id", "")
    notes = task.get("notes", "No details")
    due = _format_due_for_voice(task.get("due"))
    status = task.get("status", "needsAction")
    updated = task.get("updated", "Unknown")
    return (
        f"Task ID: {task_id}\n"
        f"Title: {title}\n"
        f"Details: {notes}\n"
        f"Due: {due}\n"
        f"Status: {status}\n"
        f"Last Updated: {updated}"
    )



def _list_task_items(credentials, *, include_completed: bool = True, max_results: int = 100) -> list[dict]:
    service = _tasks_service(credentials)
    result = service.tasks().list(
        tasklist="@default",
        showCompleted=include_completed,
        showHidden=False,
        maxResults=max_results,
    ).execute()
    return result.get("items", [])



def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())



def _find_best_task_match(credentials, task_reference: str, *, include_completed: bool = True) -> tuple[dict | None, list[dict]]:
    query = _normalize_text(task_reference)
    if not query:
        return None, []

    tasks = _list_task_items(credentials, include_completed=include_completed)
    scored: list[tuple[int, dict]] = []
    for task in tasks:
        title = _normalize_text(task.get("title"))
        notes = _normalize_text(task.get("notes"))
        haystack = f"{title} {notes}".strip()
        score = 0
        if title == query:
            score = 100
        elif query and query in title:
            score = 80
        elif haystack == query:
            score = 70
        elif query and query in haystack:
            score = 60
        else:
            query_tokens = set(query.split())
            haystack_tokens = set(haystack.split())
            overlap = len(query_tokens & haystack_tokens)
            if overlap:
                score = overlap * 10
        if score:
            scored.append((score, task))

    scored.sort(key=lambda item: (item[0], item[1].get("updated", "")), reverse=True)
    matches = [task for _, task in scored]
    return (matches[0] if matches else None), matches[:5]



def _resolve_task_or_message(credentials, task_reference: str, *, include_completed: bool = True) -> tuple[dict | None, str | None]:
    best, matches = _find_best_task_match(credentials, task_reference, include_completed=include_completed)
    if not matches:
        return None, f"No task matched '{task_reference}'. Try using more of the task title."
    if len(matches) > 1:
        top_titles = ", ".join(task.get("title", "Untitled") for task in matches[:3])
        best_title = best.get("title", "Untitled") if best else "Unknown"
        if best and _normalize_text(best.get("title")) == _normalize_text(task_reference):
            return best, None
        return None, (
            f"I found multiple possible tasks for '{task_reference}': {top_titles}. "
            f"Please be more specific. The closest match was '{best_title}'."
        )
    return best, None



def create_task(credentials, params: CreateTaskInput) -> str:
    try:
        service = _tasks_service(credentials)
        task = {"title": params.title, "notes": params.notes}
        due = _normalize_due(params.due)
        if due:
            task["due"] = due
        result = service.tasks().insert(tasklist="@default", body=task).execute()
        return "Task created successfully.\n" + _format_task(result)
    except Exception as e:
        return f"Error creating task: {str(e)}"



def list_tasks(credentials, params: ListTasksInput) -> str:
    try:
        items = _list_task_items(credentials, include_completed=params.show_completed, max_results=params.max_results)
        if not items:
            return "No tasks found."
        return "\n\n---\n\n".join(_format_task(task) for task in items)
    except Exception as e:
        return f"Error listing tasks: {str(e)}"



def get_task(credentials, params: GetTaskInput) -> str:
    try:
        service = _tasks_service(credentials)
        task = service.tasks().get(tasklist="@default", task=params.task_id).execute()
        return _format_task(task)
    except Exception as e:
        return f"Error fetching task: {str(e)}"



def get_task_by_reference(credentials, params: GetTaskByReferenceInput) -> str:
    try:
        task, error = _resolve_task_or_message(credentials, params.task_reference)
        if error:
            return error
        return _format_task(task)
    except Exception as e:
        return f"Error fetching task by reference: {str(e)}"



def find_task(credentials, params: FindTaskInput) -> str:
    try:
        best, matches = _find_best_task_match(credentials, params.query, include_completed=params.include_completed)
        if not matches:
            return f"No task matched '{params.query}'."
        response = [f"Best match for '{params.query}':", _format_task(best)]
        if len(matches) > 1:
            response.append("Other possible matches:")
            response.extend(f"- {task.get('title', 'Untitled')} ({task.get('id', '')})" for task in matches[1:4])
        return "\n".join(response)
    except Exception as e:
        return f"Error finding task: {str(e)}"



def update_task(credentials, params: UpdateTaskInput) -> str:
    try:
        service = _tasks_service(credentials)
        existing = service.tasks().get(tasklist="@default", task=params.task_id).execute()

        if params.title is not None:
            existing["title"] = params.title
        if params.notes is not None:
            existing["notes"] = params.notes
        if params.due is not None:
            normalized_due = _normalize_due(params.due)
            if normalized_due:
                existing["due"] = normalized_due
        if params.status is not None:
            existing["status"] = params.status
            if params.status == "completed":
                existing["completed"] = dt.datetime.now(dt.timezone.utc).strftime(RFC3339_FORMAT)
            elif params.status == "needsAction":
                existing.pop("completed", None)

        updated = service.tasks().update(
            tasklist="@default",
            task=params.task_id,
            body=existing,
        ).execute()
        return "Task updated successfully.\n" + _format_task(updated)
    except Exception as e:
        return f"Error updating task: {str(e)}"



def update_task_by_reference(credentials, params: UpdateTaskByReferenceInput) -> str:
    try:
        task, error = _resolve_task_or_message(credentials, params.task_reference)
        if error:
            return error
        return update_task(
            credentials,
            UpdateTaskInput(
                task_id=task["id"],
                title=params.title,
                notes=params.notes,
                due=params.due,
                status=params.status,
            ),
        )
    except Exception as e:
        return f"Error updating task by reference: {str(e)}"



def set_task_due_date(credentials, params: SetTaskDueDateInput) -> str:
    return update_task(credentials, UpdateTaskInput(task_id=params.task_id, due=params.due))



def set_task_due_date_by_reference(credentials, params: DueDateByReferenceInput) -> str:
    try:
        task, error = _resolve_task_or_message(credentials, params.task_reference)
        if error:
            return error
        return set_task_due_date(credentials, SetTaskDueDateInput(task_id=task["id"], due=params.due))
    except Exception as e:
        return f"Error setting task deadline by reference: {str(e)}"



def complete_task(credentials, params: CompleteTaskInput) -> str:
    return update_task(credentials, UpdateTaskInput(task_id=params.task_id, status="completed"))



def complete_task_by_reference(credentials, params: CompleteTaskByReferenceInput) -> str:
    try:
        task, error = _resolve_task_or_message(credentials, params.task_reference)
        if error:
            return error
        return complete_task(credentials, CompleteTaskInput(task_id=task["id"]))
    except Exception as e:
        return f"Error completing task by reference: {str(e)}"



def clear_task_due_date(credentials, params: ClearTaskDueDateInput) -> str:
    try:
        service = _tasks_service(credentials)
        existing = service.tasks().get(tasklist="@default", task=params.task_id).execute()
        existing.pop("due", None)
        updated = service.tasks().update(tasklist="@default", task=params.task_id, body=existing).execute()
        return "Task due date cleared successfully.\n" + _format_task(updated)
    except Exception as e:
        return f"Error clearing task due date: {str(e)}"



def clear_task_due_date_by_reference(credentials, params: DestructiveTaskByReferenceInput) -> str:
    try:
        task, error = _resolve_task_or_message(credentials, params.task_reference)
        if error:
            return error
        task_id = task["id"]
        task_title = task.get("title", "Untitled")
        if not params.confirmation_token:
            token = pending_task_actions.create(action="clear_due_date", task_id=task_id, task_title=task_title)
            return (
                f"Confirmation required before removing the deadline from '{task_title}'. "
                f"If the user confirms, call this tool again with confirmation_token='{token}'."
            )
        valid, error_message = pending_task_actions.validate(
            token=params.confirmation_token,
            action="clear_due_date",
            task_id=task_id,
        )
        if not valid:
            return error_message or "Confirmation failed."
        return clear_task_due_date(credentials, ClearTaskDueDateInput(task_id=task_id))
    except Exception as e:
        return f"Error clearing task due date by reference: {str(e)}"



def delete_task(credentials, params: DeleteTaskInput) -> str:
    try:
        service = _tasks_service(credentials)
        service.tasks().delete(tasklist="@default", task=params.task_id).execute()
        return f"Task {params.task_id} deleted successfully."
    except Exception as e:
        return f"Error deleting task: {str(e)}"



def delete_task_by_reference(credentials, params: DestructiveTaskByReferenceInput) -> str:
    try:
        task, error = _resolve_task_or_message(credentials, params.task_reference)
        if error:
            return error
        task_id = task["id"]
        task_title = task.get("title", "Untitled")
        if not params.confirmation_token:
            token = pending_task_actions.create(action="delete", task_id=task_id, task_title=task_title)
            return (
                f"Confirmation required before deleting '{task_title}'. "
                f"If the user confirms, call this tool again with confirmation_token='{token}'."
            )
        valid, error_message = pending_task_actions.validate(
            token=params.confirmation_token,
            action="delete",
            task_id=task_id,
        )
        if not valid:
            return error_message or "Confirmation failed."
        return delete_task(credentials, DeleteTaskInput(task_id=task_id))
    except Exception as e:
        return f"Error deleting task by reference: {str(e)}"
