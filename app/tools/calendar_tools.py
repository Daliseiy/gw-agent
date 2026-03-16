from __future__ import annotations

import datetime as dt
import secrets
from typing import Optional

from googleapiclient.discovery import build
from pydantic import BaseModel, Field

from app.tools.people_tools import resolve_contact_emails

RFC3339_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
CONFIRMATION_TTL_MINUTES = 10


class PendingCalendarActionStore:
    def __init__(self) -> None:
        self._actions: dict[str, dict] = {}

    def create(self, *, action: str, event_id: str, event_title: str) -> str:
        token = secrets.token_urlsafe(16)
        self._actions[token] = {
            "action": action,
            "event_id": event_id,
            "event_title": event_title,
            "expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=CONFIRMATION_TTL_MINUTES),
        }
        return token

    def validate(self, *, token: str, action: str, event_id: str) -> tuple[bool, str | None]:
        payload = self._actions.get(token)
        if not payload:
            return False, "Invalid confirmation token."
        if payload["expires_at"] < dt.datetime.now(dt.timezone.utc):
            self._actions.pop(token, None)
            return False, "Confirmation token expired. Please ask again."
        if payload["action"] != action or payload["event_id"] != event_id:
            return False, "Confirmation token does not match this calendar action."
        self._actions.pop(token, None)
        return True, None


pending_calendar_actions = PendingCalendarActionStore()


class CreateEventInput(BaseModel):
    summary: str = Field(..., description="Title of the event.")
    start_time: str = Field(..., description="RFC3339 or ISO 8601 start datetime.")
    end_time: str = Field(..., description="RFC3339 or ISO 8601 end datetime.")
    attendee_emails: list[str] = Field(default_factory=list, description="Attendee email addresses.")
    description: str = Field(default="", description="Optional event description.")
    timezone: str = Field(default="UTC", description="IANA timezone name, for example Africa/Lagos.")


class CreateEventForContactsInput(BaseModel):
    summary: str = Field(..., description="Title of the event.")
    start_time: str = Field(..., description="RFC3339 or ISO 8601 start datetime.")
    end_time: str = Field(..., description="RFC3339 or ISO 8601 end datetime.")
    attendee_names: list[str] = Field(default_factory=list, description="Contact names spoken by the user.")
    description: str = Field(default="", description="Optional event description.")
    timezone: str = Field(default="UTC", description="IANA timezone name, for example Africa/Lagos.")


class GetUpcomingEventsInput(BaseModel):
    max_results: int = Field(default=10, ge=1, le=20, description="Maximum number of upcoming events.")


class FindEventInput(BaseModel):
    query: str = Field(..., description="Natural language event title or keyword.")
    days_ahead: int = Field(default=30, ge=1, le=365, description="How far ahead to search for matching events.")


class GetEventByReferenceInput(BaseModel):
    event_reference: str = Field(..., description="Natural language event title or keyword.")
    days_ahead: int = Field(default=30, ge=1, le=365)


class UpdateEventByReferenceInput(BaseModel):
    event_reference: str = Field(..., description="Natural language event title or keyword.")
    summary: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    description: Optional[str] = None
    attendee_emails: Optional[list[str]] = None
    timezone: Optional[str] = None
    days_ahead: int = Field(default=30, ge=1, le=365)


class UpdateEventAttendeesByContactInput(BaseModel):
    event_reference: str = Field(..., description="Natural language event title or keyword.")
    attendee_names: list[str] = Field(default_factory=list, description="Contact names to set as attendees.")
    days_ahead: int = Field(default=30, ge=1, le=365)


class DeleteEventByReferenceInput(BaseModel):
    event_reference: str = Field(..., description="Natural language event title or keyword.")
    confirmation_token: Optional[str] = Field(default=None, description="Confirmation token from the previous tool response.")
    days_ahead: int = Field(default=30, ge=1, le=365)


def _calendar_service(credentials):
    return build("calendar", "v3", credentials=credentials)


def _normalize_datetime(value: str) -> str:
    cleaned = value.strip()
    parsed = dt.datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).strftime(RFC3339_FORMAT)


def _voice_datetime(value: str | None) -> str:
    if not value:
        return "Unknown"
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M %Z").strip()
    except ValueError:
        return value


def _format_event(event: dict) -> str:
    start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
    end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
    attendees = ", ".join(a.get("email", "") for a in event.get("attendees", []) if a.get("email")) or "None"
    meet_link = event.get("hangoutLink") or "None"
    return (
        f"Event ID: {event.get('id', '')}\n"
        f"Title: {event.get('summary', 'Untitled event')}\n"
        f"Starts: {_voice_datetime(start)}\n"
        f"Ends: {_voice_datetime(end)}\n"
        f"Attendees: {attendees}\n"
        f"Description: {event.get('description', 'No description')}\n"
        f"Meet Link: {meet_link}"
    )


def _list_events(credentials, *, days_ahead: int = 30, max_results: int = 50) -> list[dict]:
    service = _calendar_service(credentials)
    now = dt.datetime.now(dt.timezone.utc)
    time_min = now.isoformat()
    time_max = (now + dt.timedelta(days=days_ahead)).isoformat()
    result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=max_results,
    ).execute()
    return result.get("items", [])


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _find_best_event_match(credentials, event_reference: str, *, days_ahead: int = 30) -> tuple[dict | None, list[dict]]:
    query = _normalize_text(event_reference)
    if not query:
        return None, []
    events = _list_events(credentials, days_ahead=days_ahead)
    scored: list[tuple[int, dict]] = []
    for event in events:
        summary = _normalize_text(event.get("summary"))
        description = _normalize_text(event.get("description"))
        haystack = f"{summary} {description}".strip()
        score = 0
        if summary == query:
            score = 100
        elif query and query in summary:
            score = 80
        elif haystack == query:
            score = 70
        elif query and query in haystack:
            score = 60
        else:
            overlap = len(set(query.split()) & set(haystack.split()))
            if overlap:
                score = overlap * 10
        if score:
            scored.append((score, event))
    scored.sort(key=lambda item: (item[0], item[1].get("updated", "")), reverse=True)
    matches = [event for _, event in scored]
    return (matches[0] if matches else None), matches[:5]


def _resolve_event_or_message(credentials, event_reference: str, *, days_ahead: int = 30) -> tuple[dict | None, str | None]:
    best, matches = _find_best_event_match(credentials, event_reference, days_ahead=days_ahead)
    if not matches:
        return None, f"No event matched '{event_reference}'. Try using more of the title."
    if len(matches) > 1:
        top_titles = ", ".join(event.get("summary", "Untitled event") for event in matches[:3])
        if best and _normalize_text(best.get("summary")) == _normalize_text(event_reference):
            return best, None
        return None, f"I found multiple possible events for '{event_reference}': {top_titles}. Please be more specific."
    return best, None


def _resolve_attendee_emails_from_names(credentials, attendee_names: list[str]) -> tuple[list[str], str | None]:
    emails, errors = resolve_contact_emails(credentials, attendee_names)
    if errors:
        return [], " | ".join(errors)
    return emails, None


def create_event(credentials, params: CreateEventInput) -> str:
    try:
        service = _calendar_service(credentials)
        event_body = {
            "summary": params.summary,
            "description": params.description,
            "start": {"dateTime": _normalize_datetime(params.start_time), "timeZone": params.timezone},
            "end": {"dateTime": _normalize_datetime(params.end_time), "timeZone": params.timezone},
            "attendees": [{"email": email} for email in params.attendee_emails],
            "conferenceData": {
                "createRequest": {
                    "requestId": secrets.token_hex(8),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        event = service.events().insert(
            calendarId="primary",
            body=event_body,
            conferenceDataVersion=1,
            sendUpdates="all" if params.attendee_emails else "none",
        ).execute()
        return "Event created successfully.\n" + _format_event(event)
    except Exception as e:
        return f"Failed to create event. Error: {str(e)}"


def create_event_for_contacts(credentials, params: CreateEventForContactsInput) -> str:
    try:
        attendee_emails, error = _resolve_attendee_emails_from_names(credentials, params.attendee_names)
        if error:
            return error
        return create_event(
            credentials,
            CreateEventInput(
                summary=params.summary,
                start_time=params.start_time,
                end_time=params.end_time,
                attendee_emails=attendee_emails,
                description=params.description,
                timezone=params.timezone,
            ),
        )
    except Exception as e:
        return f"Failed to create event for contacts. Error: {str(e)}"


def get_upcoming_events(credentials, params: GetUpcomingEventsInput) -> str:
    try:
        events = _list_events(credentials, days_ahead=30, max_results=params.max_results)
        if not events:
            return "You have no upcoming calendar events."
        return "\n\n---\n\n".join(_format_event(event) for event in events)
    except Exception as e:
        return f"Failed to retrieve upcoming events. Error: {str(e)}"


def find_event(credentials, params: FindEventInput) -> str:
    try:
        best, matches = _find_best_event_match(credentials, params.query, days_ahead=params.days_ahead)
        if not matches:
            return f"No event matched '{params.query}'."
        lines = [f"Found {len(matches)} possible event(s). Closest matches:"]
        for event in matches[:5]:
            lines.append(_format_event(event))
        if best:
            lines.append(f"Best match: {best.get('summary', 'Untitled event')}")
        return "\n\n---\n\n".join(lines)
    except Exception as e:
        return f"Failed to find event. Error: {str(e)}"


def get_event_by_reference(credentials, params: GetEventByReferenceInput) -> str:
    try:
        event, error = _resolve_event_or_message(credentials, params.event_reference, days_ahead=params.days_ahead)
        if error:
            return error
        return _format_event(event)
    except Exception as e:
        return f"Failed to get event. Error: {str(e)}"


def update_event_by_reference(credentials, params: UpdateEventByReferenceInput) -> str:
    try:
        event, error = _resolve_event_or_message(credentials, params.event_reference, days_ahead=params.days_ahead)
        if error:
            return error
        service = _calendar_service(credentials)
        if params.summary is not None:
            event["summary"] = params.summary
        if params.description is not None:
            event["description"] = params.description
        if params.attendee_emails is not None:
            event["attendees"] = [{"email": email} for email in params.attendee_emails]
        if params.start_time is not None:
            event.setdefault("start", {})["dateTime"] = _normalize_datetime(params.start_time)
        if params.end_time is not None:
            event.setdefault("end", {})["dateTime"] = _normalize_datetime(params.end_time)
        if params.timezone is not None:
            event.setdefault("start", {})["timeZone"] = params.timezone
            event.setdefault("end", {})["timeZone"] = params.timezone
        updated = service.events().update(
            calendarId="primary",
            eventId=event["id"],
            body=event,
            conferenceDataVersion=1,
            sendUpdates="all",
        ).execute()
        return "Event updated successfully.\n" + _format_event(updated)
    except Exception as e:
        return f"Failed to update event. Error: {str(e)}"


def update_event_attendees_by_contact(credentials, params: UpdateEventAttendeesByContactInput) -> str:
    try:
        attendee_emails, error = _resolve_attendee_emails_from_names(credentials, params.attendee_names)
        if error:
            return error
        return update_event_by_reference(
            credentials,
            UpdateEventByReferenceInput(
                event_reference=params.event_reference,
                attendee_emails=attendee_emails,
                days_ahead=params.days_ahead,
            ),
        )
    except Exception as e:
        return f"Failed to update event attendees. Error: {str(e)}"


def delete_event_by_reference(credentials, params: DeleteEventByReferenceInput) -> str:
    try:
        event, error = _resolve_event_or_message(credentials, params.event_reference, days_ahead=params.days_ahead)
        if error:
            return error
        event_id = event["id"]
        event_title = event.get("summary", "Untitled event")
        if not params.confirmation_token:
            token = pending_calendar_actions.create(action="delete", event_id=event_id, event_title=event_title)
            return (
                f"Confirmation required before deleting '{event_title}'. "
                f"If the user confirms, call this tool again with confirmation_token='{token}'."
            )
        valid, error_message = pending_calendar_actions.validate(
            token=params.confirmation_token,
            action="delete",
            event_id=event_id,
        )
        if not valid:
            return error_message or "Confirmation failed."
        _calendar_service(credentials).events().delete(calendarId="primary", eventId=event_id, sendUpdates="all").execute()
        return f"Event '{event_title}' deleted successfully."
    except Exception as e:
        return f"Failed to delete event. Error: {str(e)}"
