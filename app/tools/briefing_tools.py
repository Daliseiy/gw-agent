from __future__ import annotations

import datetime as dt
import email.utils
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from pydantic import BaseModel, Field


class GetDailyExecutiveBriefingInput(BaseModel):
    max_events: int = Field(default=5, ge=1, le=10, description="Maximum number of agenda items to include.")
    max_tasks: int = Field(default=5, ge=1, le=10, description="Maximum number of tasks to include.")
    max_emails: int = Field(default=5, ge=1, le=10, description="Maximum number of important unread emails to include.")


def _calendar_service(credentials):
    return build("calendar", "v3", credentials=credentials)


def _tasks_service(credentials):
    return build("tasks", "v1", credentials=credentials)


def _gmail_service(credentials):
    return build("gmail", "v1", credentials=credentials)


def _get_user_timezone(credentials) -> str:
    try:
        settings = _calendar_service(credentials).settings().get(setting="timezone").execute()
        return settings.get("value") or "UTC"
    except Exception:
        return "UTC"


def _get_local_now(timezone_name: str) -> dt.datetime:
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = dt.timezone.utc
    return dt.datetime.now(tz)


def _format_time(value: str | None, timezone_name: str) -> str:
    if not value:
        return "Unknown time"
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return value


def _format_due_date(value: str | None, timezone_name: str) -> str:
    if not value:
        return "No due date"
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%b %d, %I:%M %p").replace(" 0", " ")
    except Exception:
        return value


def _parse_email_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def _today_agenda(credentials, timezone_name: str, max_events: int) -> list[dict]:
    now_local = _get_local_now(timezone_name)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + dt.timedelta(days=1)
    service = _calendar_service(credentials)
    result = service.events().list(
        calendarId="primary",
        timeMin=start_local.astimezone(dt.timezone.utc).isoformat(),
        timeMax=end_local.astimezone(dt.timezone.utc).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=max_events,
    ).execute()
    return result.get("items", [])


def _open_tasks(credentials, max_tasks: int) -> list[dict]:
    service = _tasks_service(credentials)
    result = service.tasks().list(
        tasklist="@default",
        showCompleted=False,
        showHidden=False,
        maxResults=50,
    ).execute()
    tasks = result.get("items", [])

    def task_sort_key(task: dict):
        due = task.get("due") or "9999-12-31T23:59:59.000Z"
        updated = task.get("updated") or ""
        return (due, updated)

    return sorted(tasks, key=task_sort_key)[:max_tasks]


def _important_unread_emails(credentials, max_emails: int) -> list[dict]:
    service = _gmail_service(credentials)
    results = service.users().messages().list(
        userId="me",
        q="in:inbox is:unread",
        maxResults=15,
    ).execute()
    messages = results.get("messages", [])
    ranked: list[tuple[int, dt.datetime, dict]] = []
    for message in messages:
        msg_data = service.users().messages().get(
            userId="me",
            id=message["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = msg_data.get("payload", {}).get("headers", [])
        labels = set(msg_data.get("labelIds", []))
        sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown sender")
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No subject")
        date = next((h["value"] for h in headers if h["name"] == "Date"), None)
        snippet = msg_data.get("snippet", "")
        score = 0
        if "IMPORTANT" in labels:
            score += 100
        if "CATEGORY_PERSONAL" in labels or "CATEGORY_UPDATES" not in labels:
            score += 10
        lowered_sender = sender.lower()
        if any(token in lowered_sender for token in ["boss", "ceo", "founder", "hr", "recruit", "manager"]):
            score += 5
        parsed_date = _parse_email_date(date) or dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
        ranked.append(
            score,
        )
        ranked[-1] = (score, parsed_date, {
            "from": sender,
            "subject": subject,
            "date": date or "Unknown date",
            "snippet": snippet,
            "labels": sorted(labels),
        })
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[:max_emails]]


def get_daily_executive_briefing(credentials, params: GetDailyExecutiveBriefingInput) -> str:
    try:
        timezone_name = _get_user_timezone(credentials)
        local_now = _get_local_now(timezone_name)

        events = _today_agenda(credentials, timezone_name, params.max_events)
        tasks = _open_tasks(credentials, params.max_tasks)
        emails = _important_unread_emails(credentials, params.max_emails)

        lines: list[str] = []
        lines.append(
            f"Daily executive briefing for {local_now.strftime('%A, %B %d')} ({timezone_name})."
        )

        if events:
            lines.append(f"You have {len(events)} agenda item{'s' if len(events) != 1 else ''} today.")
            for event in events:
                start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
                end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
                summary = event.get("summary", "Untitled event")
                when = _format_time(start, timezone_name)
                end_when = _format_time(end, timezone_name) if event.get("start", {}).get("dateTime") else None
                if end_when:
                    lines.append(f"- {when} to {end_when}: {summary}.")
                else:
                    lines.append(f"- {summary}.")
        else:
            lines.append("Your calendar is clear today.")

        if tasks:
            overdue_count = 0
            due_today_count = 0
            today_local_date = local_now.date()
            lines.append(f"You have {len(tasks)} open task{'s' if len(tasks) != 1 else ''} in focus.")
            for task in tasks:
                due_value = task.get("due")
                due_phrase = "No due date"
                if due_value:
                    try:
                        due_dt = dt.datetime.fromisoformat(due_value.replace("Z", "+00:00")).astimezone(ZoneInfo(timezone_name))
                        if due_dt.date() < today_local_date:
                            overdue_count += 1
                            due_phrase = f"Overdue since {due_dt.strftime('%b %d')}"
                        elif due_dt.date() == today_local_date:
                            due_today_count += 1
                            due_phrase = f"Due today at {due_dt.strftime('%I:%M %p').lstrip('0')}"
                        else:
                            due_phrase = f"Due {_format_due_date(due_value, timezone_name)}"
                    except Exception:
                        due_phrase = f"Due {_format_due_date(due_value, timezone_name)}"
                lines.append(f"- {task.get('title', 'Untitled task')}: {due_phrase}.")
            if overdue_count:
                lines.append(f"{overdue_count} task{'s are' if overdue_count != 1 else ' is'} overdue.")
            elif due_today_count:
                lines.append(f"{due_today_count} task{'s are' if due_today_count != 1 else ' is'} due today.")
        else:
            lines.append("You have no open tasks right now.")

        if emails:
            lines.append(f"You have {len(emails)} important unread email{'s' if len(emails) != 1 else ''} worth reviewing.")
            for email_item in emails:
                snippet = (email_item.get("snippet") or "").replace("\n", " ").strip()
                if len(snippet) > 120:
                    snippet = snippet[:117].rstrip() + "..."
                lines.append(
                    f"- From {email_item['from']}, subject {email_item['subject']}. {snippet}".strip()
                )
        else:
            lines.append("You have no important unread emails right now.")

        lines.append("End of briefing.")
        return "\n".join(lines)
    except Exception as exc:
        return f"Failed to prepare the daily executive briefing. Error: {str(exc)}"
