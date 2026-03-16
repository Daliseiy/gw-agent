from __future__ import annotations

from app.tools.briefing_tools import GetDailyExecutiveBriefingInput, get_daily_executive_briefing
from app.tools.calendar_tools import (
    CreateEventForContactsInput,
    CreateEventInput,
    DeleteEventByReferenceInput,
    FindEventInput,
    GetEventByReferenceInput,
    GetUpcomingEventsInput,
    UpdateEventAttendeesByContactInput,
    UpdateEventByReferenceInput,
    create_event,
    create_event_for_contacts,
    delete_event_by_reference,
    find_event,
    get_event_by_reference,
    get_upcoming_events,
    update_event_attendees_by_contact,
    update_event_by_reference,
)
from app.tools.gmail_tools import (
    DraftEmailInput,
    DraftEmailToContactInput,
    GetUnreadEmailsInput,
    SendDraftedEmailInput,
    SendEmailInput,
    SendEmailToContactInput,
    draft_email_for_review,
    draft_email_to_contact_for_review,
    get_unread_emails,
    send_drafted_email,
    send_email,
    send_email_to_contact,
)
from app.tools.people_tools import (
    GetContactEmailInput,
    ResolveContactsInput,
    SearchContactInput,
    get_contact_email,
    resolve_contacts,
    search_contact,
)
from app.tools.task_tools import (
    ClearTaskDueDateInput,
    CompleteTaskByReferenceInput,
    CompleteTaskInput,
    CreateTaskInput,
    DeleteTaskInput,
    DestructiveTaskByReferenceInput,
    DueDateByReferenceInput,
    FindTaskInput,
    GetTaskByReferenceInput,
    GetTaskInput,
    ListTasksInput,
    SetTaskDueDateInput,
    UpdateTaskByReferenceInput,
    UpdateTaskInput,
    clear_task_due_date,
    clear_task_due_date_by_reference,
    complete_task,
    complete_task_by_reference,
    create_task,
    delete_task,
    delete_task_by_reference,
    find_task,
    get_task,
    get_task_by_reference,
    list_tasks,
    set_task_due_date,
    set_task_due_date_by_reference,
    update_task,
    update_task_by_reference,
)


def build_workspace_tools(credentials):
    def agent_schedule_meeting(
        summary: str,
        start_time: str,
        end_time: str,
        attendee_emails: list[str],
        description: str = "",
        timezone: str = "UTC",
    ) -> str:
        """Schedules a calendar event with a Google Meet link. Times must be strict ISO 8601."""
        return create_event(
            credentials,
            CreateEventInput(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                attendee_emails=attendee_emails,
                description=description,
                timezone=timezone,
            ),
        )

    def agent_schedule_meeting_for_contacts(
        summary: str,
        start_time: str,
        end_time: str,
        attendee_names: list[str],
        description: str = "",
        timezone: str = "UTC",
    ) -> str:
        """Schedules a meeting using spoken Google Contact names instead of email addresses."""
        return create_event_for_contacts(
            credentials,
            CreateEventForContactsInput(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                attendee_names=attendee_names,
                description=description,
                timezone=timezone,
            ),
        )

    def agent_check_schedule(max_results: int = 10) -> str:
        """Checks the user's upcoming calendar events."""
        return get_upcoming_events(credentials, GetUpcomingEventsInput(max_results=max_results))

    def agent_daily_executive_briefing(max_events: int = 5, max_tasks: int = 5, max_emails: int = 5) -> str:
        """Builds a short daily executive briefing covering today's agenda, open tasks, and important unread emails."""
        return get_daily_executive_briefing(
            credentials,
            GetDailyExecutiveBriefingInput(max_events=max_events, max_tasks=max_tasks, max_emails=max_emails),
        )

    def agent_find_event(event_reference: str, days_ahead: int = 30) -> str:
        """Finds a calendar event by a natural language title or keyword."""
        return find_event(credentials, FindEventInput(query=event_reference, days_ahead=days_ahead))

    def agent_get_event_by_reference(event_reference: str, days_ahead: int = 30) -> str:
        """Gets the details of one calendar event using a natural language title or keyword."""
        return get_event_by_reference(
            credentials,
            GetEventByReferenceInput(event_reference=event_reference, days_ahead=days_ahead),
        )

    def agent_update_event_by_reference(
        event_reference: str,
        summary: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        description: str | None = None,
        attendee_emails: list[str] | None = None,
        timezone: str | None = None,
        days_ahead: int = 30,
    ) -> str:
        """Updates a calendar event using a natural language event reference."""
        return update_event_by_reference(
            credentials,
            UpdateEventByReferenceInput(
                event_reference=event_reference,
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                description=description,
                attendee_emails=attendee_emails,
                timezone=timezone,
                days_ahead=days_ahead,
            ),
        )

    def agent_update_event_attendees_by_contact(
        event_reference: str,
        attendee_names: list[str],
        days_ahead: int = 30,
    ) -> str:
        """Updates an event's attendees using spoken Google Contact names."""
        return update_event_attendees_by_contact(
            credentials,
            UpdateEventAttendeesByContactInput(
                event_reference=event_reference,
                attendee_names=attendee_names,
                days_ahead=days_ahead,
            ),
        )

    def agent_delete_event_by_reference(
        event_reference: str,
        confirmation_token: str | None = None,
        days_ahead: int = 30,
    ) -> str:
        """Deletes a calendar event by natural language reference. First call returns a confirmation token; call again with that token only after the user confirms."""
        return delete_event_by_reference(
            credentials,
            DeleteEventByReferenceInput(
                event_reference=event_reference,
                confirmation_token=confirmation_token,
                days_ahead=days_ahead,
            ),
        )

    def agent_find_contact(name: str) -> str:
        """Looks up a person's name in Google Contacts to find their email address."""
        return search_contact(credentials, SearchContactInput(name=name))

    def agent_get_contact_email(name: str) -> str:
        """Resolves one spoken Google Contact name to an email address."""
        return get_contact_email(credentials, GetContactEmailInput(name=name))

    def agent_resolve_contacts(names: list[str]) -> str:
        """Resolves multiple spoken Google Contact names to email addresses."""
        return resolve_contacts(credentials, ResolveContactsInput(names=names))

    def agent_send_email(to_email: str, subject: str, body: str) -> str:
        """Sends an email from the authenticated user. Use sparingly; for voice flows prefer drafting first and sending only after explicit confirmation."""
        return send_email(credentials, SendEmailInput(to_email=to_email, subject=subject, body=body))

    def agent_draft_email_for_review(to_email: str, subject: str, body: str) -> str:
        """Drafts an email for voice review. Use this before sending when the user is composing a new email over voice."""
        return draft_email_for_review(
            credentials,
            DraftEmailInput(to_email=to_email, subject=subject, body=body),
        )

    def agent_send_drafted_email(confirmation_token: str) -> str:
        """Sends a previously drafted email after the user explicitly confirms."""
        return send_drafted_email(
            credentials,
            SendDraftedEmailInput(confirmation_token=confirmation_token),
        )

    def agent_send_email_to_contact(contact_name: str, subject: str, body: str) -> str:
        """Sends an email to a Google Contact by spoken name. Prefer drafting first for brand new emails over voice."""
        return send_email_to_contact(
            credentials,
            SendEmailToContactInput(contact_name=contact_name, subject=subject, body=body),
        )

    def agent_draft_email_to_contact_for_review(contact_name: str, subject: str, body: str) -> str:
        """Drafts an email to a Google Contact by spoken name and returns a confirmation token for a later send."""
        return draft_email_to_contact_for_review(
            credentials,
            DraftEmailToContactInput(contact_name=contact_name, subject=subject, body=body),
        )

    def agent_get_unread_emails(max_results: int = 5) -> str:
        """Reads unread inbox emails with sender, subject, date, and snippet."""
        return get_unread_emails(credentials, GetUnreadEmailsInput(max_results=max_results))

    def agent_create_task(title: str, notes: str = "", due: str | None = None) -> str:
        """Creates a task, optionally with details and a deadline."""
        return create_task(credentials, CreateTaskInput(title=title, notes=notes, due=due))

    def agent_list_tasks(show_completed: bool = False, max_results: int = 20) -> str:
        """Lists tasks with title, details, status, timeline, and due date."""
        return list_tasks(credentials, ListTasksInput(show_completed=show_completed, max_results=max_results))

    def agent_find_task(task_reference: str, include_completed: bool = True) -> str:
        """Finds a task by a natural language title or keyword instead of a task ID."""
        return find_task(credentials, FindTaskInput(query=task_reference, include_completed=include_completed))

    def agent_get_task(task_id: str) -> str:
        """Gets the details of one specific task by task ID."""
        return get_task(credentials, GetTaskInput(task_id=task_id))

    def agent_get_task_by_reference(task_reference: str) -> str:
        """Gets one task using a natural language task title or keyword."""
        return get_task_by_reference(credentials, GetTaskByReferenceInput(task_reference=task_reference))

    def agent_update_task(
        task_id: str,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
        status: str | None = None,
    ) -> str:
        """Updates a task title, notes, deadline, or status using a task ID."""
        return update_task(
            credentials,
            UpdateTaskInput(task_id=task_id, title=title, notes=notes, due=due, status=status),
        )

    def agent_update_task_by_reference(
        task_reference: str,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
        status: str | None = None,
    ) -> str:
        """Updates a task title, notes, deadline, or status using a natural language task reference."""
        return update_task_by_reference(
            credentials,
            UpdateTaskByReferenceInput(task_reference=task_reference, title=title, notes=notes, due=due, status=status),
        )

    def agent_set_task_deadline(task_id: str, due: str) -> str:
        """Sets or updates a task deadline using a task ID."""
        return set_task_due_date(credentials, SetTaskDueDateInput(task_id=task_id, due=due))

    def agent_set_task_deadline_by_reference(task_reference: str, due: str) -> str:
        """Sets or updates a task deadline using a natural language task reference."""
        return set_task_due_date_by_reference(credentials, DueDateByReferenceInput(task_reference=task_reference, due=due))

    def agent_clear_task_deadline(task_id: str) -> str:
        """Removes a task deadline using a task ID."""
        return clear_task_due_date(credentials, ClearTaskDueDateInput(task_id=task_id))

    def agent_clear_task_deadline_by_reference(task_reference: str, confirmation_token: str | None = None) -> str:
        """Removes a task deadline by natural language reference. First call returns a confirmation token; call again with that token after the user confirms."""
        return clear_task_due_date_by_reference(
            credentials,
            DestructiveTaskByReferenceInput(task_reference=task_reference, confirmation_token=confirmation_token),
        )

    def agent_complete_task(task_id: str) -> str:
        """Marks a task as completed using a task ID."""
        return complete_task(credentials, CompleteTaskInput(task_id=task_id))

    def agent_complete_task_by_reference(task_reference: str) -> str:
        """Marks a task as completed using a natural language task reference."""
        return complete_task_by_reference(credentials, CompleteTaskByReferenceInput(task_reference=task_reference))

    def agent_delete_task(task_id: str) -> str:
        """Deletes a task by task ID. Avoid using this unless the user explicitly confirms and you already know the exact ID."""
        return delete_task(credentials, DeleteTaskInput(task_id=task_id))

    def agent_delete_task_by_reference(task_reference: str, confirmation_token: str | None = None) -> str:
        """Deletes a task by natural language reference. First call returns a confirmation token; call again with that token after the user confirms."""
        return delete_task_by_reference(
            credentials,
            DestructiveTaskByReferenceInput(task_reference=task_reference, confirmation_token=confirmation_token),
        )

    return [
        agent_daily_executive_briefing,
        agent_schedule_meeting,
        agent_schedule_meeting_for_contacts,
        agent_check_schedule,
        agent_find_event,
        agent_get_event_by_reference,
        agent_update_event_by_reference,
        agent_update_event_attendees_by_contact,
        agent_delete_event_by_reference,
        agent_find_contact,
        agent_get_contact_email,
        agent_resolve_contacts,
        agent_send_email,
        agent_send_email_to_contact,
        agent_draft_email_for_review,
        agent_draft_email_to_contact_for_review,
        agent_send_drafted_email,
        agent_get_unread_emails,
        agent_create_task,
        agent_list_tasks,
        agent_find_task,
        agent_get_task,
        agent_get_task_by_reference,
        agent_update_task,
        agent_update_task_by_reference,
        agent_set_task_deadline,
        agent_set_task_deadline_by_reference,
        agent_clear_task_deadline,
        agent_clear_task_deadline_by_reference,
        agent_complete_task,
        agent_complete_task_by_reference,
        agent_delete_task,
        agent_delete_task_by_reference,
    ]

