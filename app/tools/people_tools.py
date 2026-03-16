from __future__ import annotations

from googleapiclient.discovery import build
from pydantic import BaseModel, Field


class SearchContactInput(BaseModel):
    name: str = Field(..., description="The name of the person to look up.")
    max_results: int = Field(default=5, ge=1, le=10, description="Maximum contacts to return.")


class GetContactEmailInput(BaseModel):
    name: str = Field(..., description="Natural language contact name, such as Ada or John Doe.")


class ResolveContactsInput(BaseModel):
    names: list[str] = Field(..., description="List of contact names to resolve into email addresses.")


def _people_service(credentials):
    return build("people", "v1", credentials=credentials)


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _extract_contact_candidates(person: dict) -> list[dict]:
    names = person.get("names", [])
    emails = person.get("emailAddresses", [])
    display_name = names[0].get("displayName") if names else "Unknown"
    given_name = names[0].get("givenName") if names else ""
    family_name = names[0].get("familyName") if names else ""
    candidates: list[dict] = []
    for email in emails:
        value = email.get("value")
        if value:
            candidates.append(
                {
                    "name": display_name,
                    "given_name": given_name,
                    "family_name": family_name,
                    "email": value,
                }
            )
    return candidates


def _search_contact_candidates(credentials, query: str, *, max_results: int = 10) -> list[dict]:
    service = _people_service(credentials)
    results = service.people().searchContacts(
        query=query,
        readMask="names,emailAddresses",
        pageSize=max_results,
    ).execute()
    candidates: list[dict] = []
    for item in results.get("results", []):
        person = item.get("person", {})
        candidates.extend(_extract_contact_candidates(person))
    return candidates


def _score_candidate(query: str, candidate: dict) -> int:
    normalized_query = _normalize_text(query)
    name = _normalize_text(candidate.get("name"))
    email = _normalize_text(candidate.get("email"))
    score = 0
    if name == normalized_query:
        score = 100
    elif normalized_query and normalized_query in name:
        score = 85
    elif email == normalized_query:
        score = 80
    elif normalized_query and normalized_query in email:
        score = 70
    else:
        q_tokens = set(normalized_query.split())
        name_tokens = set(name.split())
        overlap = len(q_tokens & name_tokens)
        if overlap:
            score = overlap * 10
    return score


def _best_contact_matches(credentials, name: str, *, max_results: int = 5) -> list[dict]:
    candidates = _search_contact_candidates(credentials, name, max_results=max_results)
    scored = [(_score_candidate(name, candidate), candidate) for candidate in candidates]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(key=lambda item: (item[0], item[1].get("name", ""), item[1].get("email", "")), reverse=True)
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for _, candidate in scored:
        key = (candidate.get("name", ""), candidate.get("email", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
        if len(deduped) >= max_results:
            break
    return deduped


def resolve_contact_email(credentials, name: str) -> tuple[str | None, str | None]:
    matches = _best_contact_matches(credentials, name, max_results=5)
    if not matches:
        return None, f"No contact matched '{name}'."
    best = matches[0]
    normalized_query = _normalize_text(name)
    normalized_best = _normalize_text(best.get("name"))
    if len(matches) > 1 and normalized_best != normalized_query:
        choices = ", ".join(f"{match['name']} <{match['email']}>" for match in matches[:3])
        return None, f"I found multiple contacts for '{name}': {choices}. Please be more specific."
    return best.get("email"), None


def resolve_contact_emails(credentials, names: list[str]) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    errors: list[str] = []
    for name in names:
        email, error = resolve_contact_email(credentials, name)
        if error:
            errors.append(error)
        elif email:
            resolved.append(email)
    return resolved, errors


def search_contact(credentials, params: SearchContactInput) -> str:
    try:
        matches = _best_contact_matches(credentials, params.name, max_results=params.max_results)
        if not matches:
            return f"No contacts found for {params.name}."
        lines = [f"Found {len(matches)} contact match(es):"]
        for match in matches:
            lines.append(f"- {match['name']} <{match['email']}>")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching contacts: {str(e)}"


def get_contact_email(credentials, params: GetContactEmailInput) -> str:
    try:
        email, error = resolve_contact_email(credentials, params.name)
        if error:
            return error
        return f"{params.name}: {email}"
    except Exception as e:
        return f"Error getting contact email: {str(e)}"


def resolve_contacts(credentials, params: ResolveContactsInput) -> str:
    try:
        emails, errors = resolve_contact_emails(credentials, params.names)
        lines: list[str] = []
        if emails:
            lines.append("Resolved emails: " + ", ".join(emails))
        if errors:
            lines.append("Issues: " + " | ".join(errors))
        if not lines:
            return "No contacts resolved."
        return "\n".join(lines)
    except Exception as e:
        return f"Error resolving contacts: {str(e)}"
