from __future__ import annotations

import base64
import datetime as dt
import secrets
from email.message import EmailMessage

from googleapiclient.discovery import build
from pydantic import BaseModel, Field

from app.tools.people_tools import resolve_contact_email

CONFIRMATION_TTL_MINUTES = 10


class PendingEmailActionStore:
    def __init__(self) -> None:
        self._actions: dict[str, dict] = {}

    def create(self, *, action: str, payload: dict) -> str:
        token = secrets.token_urlsafe(16)
        self._actions[token] = {
            "action": action,
            "payload": payload,
            "expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=CONFIRMATION_TTL_MINUTES),
        }
        return token

    def validate(self, *, token: str, action: str) -> tuple[dict | None, str | None]:
        record = self._actions.get(token)
        if not record:
            return None, "Invalid confirmation token."
        if record["expires_at"] < dt.datetime.now(dt.timezone.utc):
            self._actions.pop(token, None)
            return None, "Confirmation token expired. Please compose the email again."
        if record["action"] != action:
            return None, "Confirmation token does not match this email action."
        self._actions.pop(token, None)
        return record["payload"], None


pending_email_actions = PendingEmailActionStore()


class SendEmailInput(BaseModel):
    to_email: str = Field(..., description="The email address of the recipient.")
    subject: str = Field(..., description="The subject line of the email.")
    body: str = Field(..., description="The main text content of the email.")


class SendEmailToContactInput(BaseModel):
    contact_name: str = Field(..., description="Contact name spoken by the user, such as Ada or John Doe.")
    subject: str = Field(..., description="The subject line of the email.")
    body: str = Field(..., description="The main text content of the email.")


class GetUnreadEmailsInput(BaseModel):
    max_results: int = Field(default=5, ge=1, le=20, description="Maximum number of unread emails to retrieve.")


class DraftEmailInput(BaseModel):
    to_email: str = Field(..., description="The recipient email address.")
    subject: str = Field(..., description="The subject line.")
    body: str = Field(..., description="The body text.")


class DraftEmailToContactInput(BaseModel):
    contact_name: str = Field(..., description="Contact name spoken by the user.")
    subject: str = Field(..., description="The subject line.")
    body: str = Field(..., description="The body text.")


class SendDraftedEmailInput(BaseModel):
    confirmation_token: str = Field(..., description="Confirmation token from a previous email draft response.")


def _gmail_service(credentials):
    return build("gmail", "v1", credentials=credentials)


def _build_raw_message(to_email: str, subject: str, body: str) -> dict:
    message = EmailMessage()
    message.set_content(body)
    message["To"] = to_email
    message["From"] = "me"
    message["Subject"] = subject
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": encoded_message}


def send_email(credentials, params: SendEmailInput) -> str:
    try:
        service = _gmail_service(credentials)
        service.users().messages().send(
            userId="me",
            body=_build_raw_message(params.to_email, params.subject, params.body),
        ).execute()
        return f"Email sent successfully to {params.to_email}."
    except Exception as e:
        return f"Failed to send email. Error: {str(e)}"


def send_email_to_contact(credentials, params: SendEmailToContactInput) -> str:
    try:
        email, error = resolve_contact_email(credentials, params.contact_name)
        if error:
            return error
        return send_email(
            credentials,
            SendEmailInput(to_email=email, subject=params.subject, body=params.body),
        )
    except Exception as e:
        return f"Failed to send email to contact. Error: {str(e)}"


def draft_email_for_review(credentials, params: DraftEmailInput) -> str:
    try:
        token = pending_email_actions.create(
            action="send_email",
            payload={
                "to_email": params.to_email,
                "subject": params.subject,
                "body": params.body,
            },
        )
        return (
            "Drafted email for review. Read this summary to the user and wait for explicit confirmation before sending.\n"
            f"To: {params.to_email}\n"
            f"Subject: {params.subject}\n"
            f"Body: {params.body}\n"
            f"If the user confirms, call send_drafted_email with confirmation_token='{token}'."
        )
    except Exception as e:
        return f"Failed to draft email. Error: {str(e)}"


def draft_email_to_contact_for_review(credentials, params: DraftEmailToContactInput) -> str:
    try:
        email, error = resolve_contact_email(credentials, params.contact_name)
        if error:
            return error
        return draft_email_for_review(
            credentials,
            DraftEmailInput(to_email=email, subject=params.subject, body=params.body),
        )
    except Exception as e:
        return f"Failed to draft email to contact. Error: {str(e)}"


def send_drafted_email(credentials, params: SendDraftedEmailInput) -> str:
    try:
        payload, error = pending_email_actions.validate(token=params.confirmation_token, action="send_email")
        if error:
            return error
        return send_email(
            credentials,
            SendEmailInput(
                to_email=payload["to_email"],
                subject=payload["subject"],
                body=payload["body"],
            ),
        )
    except Exception as e:
        return f"Failed to send drafted email. Error: {str(e)}"


def get_unread_emails(credentials, params: GetUnreadEmailsInput) -> str:
    try:
        service = _gmail_service(credentials)
        results = service.users().messages().list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=params.max_results,
        ).execute()
        messages = results.get("messages", [])
        if not messages:
            return "You have no unread emails."

        email_summaries = []
        for msg in messages:
            msg_data = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = msg_data.get("payload", {}).get("headers", [])
            sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown Sender")
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
            date = next((h["value"] for h in headers if h["name"] == "Date"), "Unknown Date")
            snippet = msg_data.get("snippet", "")
            email_summaries.append(
                f"From: {sender}\nSubject: {subject}\nDate: {date}\nSnippet: {snippet}"
            )
        return "\n\n---\n\n".join(email_summaries)
    except Exception as e:
        return f"Failed to retrieve emails. Error: {str(e)}"
