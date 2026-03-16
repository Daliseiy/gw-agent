# Google Workspace Voice AI Assistant

A FastAPI + Gemini Live API backend for a browser-based voice assistant that operates across Google Workspace on behalf of the signed-in user.

## What it does

- Authenticates real users with Google OAuth
- Persists per-user Google Workspace sessions across dev reloads
- Streams voice input from a browser to Gemini Live API
- Streams voice responses back to the browser
- Lets the assistant act on the user's behalf across Google Workspace
- Supports voice-safe task, calendar, and email workflows
- Executes and logs tool calls from live Gemini sessions

## Current workspace abilities

### Google Tasks

- create tasks
- list tasks
- find tasks by title or keyword
- fetch one task by ID or natural language reference
- update task title, details, status, and due date
- set or clear deadlines
- complete tasks
- delete tasks
- protect destructive task actions with confirmation tokens

### Google Calendar

- create meetings with Google Meet links
- list upcoming events
- find events by title or keyword
- fetch a single event by natural language reference
- update events by natural language reference
- delete events by natural language reference with confirmation

### Gmail

- read unread emails
- send an email directly
- draft an email for voice review
- send a drafted email only after explicit confirmation

### Google Contacts

- look up contacts by name to find email addresses

## Setup

### 1. Create Google OAuth credentials

Create an OAuth client in Google Cloud and download the client secret JSON.
Save it as `credentials.json` in the project root, or set:

```bash
export GOOGLE_CLIENT_SECRETS_FILE=/absolute/path/to/credentials.json
```

Set the callback URI in Google Cloud to match:

```bash
http://localhost:8000/auth/google/callback
```

If needed, override with:

```bash
export GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/auth/google/callback
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Gemini backend (Developer API or Vertex AI)

```bash
export GEMINI_API_KEY=your_api_key_here
# default backend is Gemini Developer API
export GEMINI_USE_VERTEX_AI=false
export GEMINI_MODEL=gemini-live-2.5-flash-preview
```

To use Vertex AI for bidirectional audio:

```bash
export GEMINI_USE_VERTEX_AI=true
export GOOGLE_CLOUD_PROJECT=your-gcp-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
# Vertex live model default (can be overridden):
export GEMINI_MODEL=gemini-2.0-flash-live-preview-04-09
```

### 5. Optional logging controls

```bash
export LOG_LEVEL=DEBUG
export LOG_HTTP_REQUESTS=true
export LOG_AUDIO_CHUNKS=false
export LOG_TOOL_PAYLOADS=true
```

### 6. Run the server

```bash
uvicorn app.main:app --reload
```

### 7. Open the demo web UI

```text
http://localhost:8000/ui
```

The sign-in/onboarding screen is served at `/ui`.  
After login, users are redirected to the assistant workspace at `/assistant`.

## Web UI flow

The frontend templates are now in `app/templates/`:

- `app/templates/index.html` for sign-in/onboarding
- `app/templates/assistant.html` for the live assistant workspace

The UI demonstrates:

- Sign in with Google from the browser
- Handling the OAuth redirect back into the UI
- Storing the returned `session_id`
- Fetching the signed-in user profile from `/me`
- Auto-opening the voice WebSocket with `/ws?session_id=...` on assistant page load
- Streaming microphone audio as binary PCM 16 kHz mono (with JSON fallback)
- Playing returned PCM 24 kHz audio from Gemini via worklet buffer (with fallback)
- Showing suggested voice commands for tasks, email, and calendar

## Auth flow

1. Frontend calls `GET /auth/google/login?return_to=/assistant`
2. Backend returns an `authorization_url`
3. Frontend redirects user to Google consent screen
4. Google redirects to `/auth/google/callback?code=...&state=...`
5. Backend exchanges the code and redirects the browser back to `/assistant?session_id=...`
6. Assistant UI fetches current user profile with `GET /me?session_id=YOUR_SESSION_ID`
7. Assistant UI connects to WebSocket using `/ws?session_id=YOUR_SESSION_ID`

## Voice WebSocket contract

### Client -> server

- Preferred: binary WebSocket frames containing raw PCM16 mono at 16 kHz.
- Backward-compatible fallback: JSON payload:
  ```json
  {
    "audio_chunk": "BASE64_PCM_16KHZ_MONO"
  }
  ```

### Server -> client

- Preferred: binary WebSocket frames containing raw PCM16 mono at 24 kHz.
- Backward-compatible fallback: JSON payload:
  ```json
  {
    "audio_chunk": "BASE64_PCM_24KHZ_MONO"
  }
  ```

### Error messages

```json
{
  "error": "description"
}
```

## Voice-safe behavior

### Natural language task and event lookup

The assistant resolves spoken references such as:

- "show my tax filing task"
- "complete the follow up with design team task"
- "set the deadline for expense report to Friday"
- "move my budget review meeting to 2 PM tomorrow"
- "delete the weekly sync event"

The backend either:

- chooses a single strong match, or
- asks the user to clarify if there are multiple likely matches

### Confirmation flows

The assistant must pause and wait for confirmation before it performs these actions:

- deleting a task
- removing a task deadline
- deleting a calendar event
- sending a drafted email

This is especially important in a voice UI, where accidental actions are easier.

### Email review flow

For new emails composed over voice, the preferred pattern is:

1. draft the email
2. read back a short summary
3. ask the user to confirm
4. send the email only after confirmation

## Notes

- Auth sessions are persisted to `.auth_sessions.json` so local dev reloads do not force re-auth.  
  You can override this path with `AUTH_SESSION_STORE_FILE`.
- Pending confirmation tokens are also stored in memory. Persist them for multi-instance deployments.
- The assistant knows the authenticated user's name and email and can act on their behalf.
- The included web UI is a practical starter frontend.
- Browser audio capture and playback can vary slightly by browser; the included page is a strong starter implementation.

## Project structure

```text
app/
  api/
    routers/
      auth.py
      health.py
      ui.py
      voice.py
  core/
    config.py
  services/
    auth.py
    workspace_tools.py
  templates/
    index.html
    assistant.html
  tools/
    ...
  main.py
main.py  # compatibility entrypoint
```


## Contact-aware voice flows

The assistant can now resolve Google Contacts from spoken names during browser voice sessions.

Examples:
- "Email Ada that I will be 10 minutes late."
- "Draft an email to John Doe about tomorrow's review."
- "Schedule a meeting with Ada and Tunde for Monday at 3 PM."
- "Add John Doe and Jane to my sprint planning meeting."

These flows use Google Contacts to resolve names to email addresses. If multiple contacts match, the assistant asks for clarification instead of guessing.


## Daily executive briefing

The assistant can now generate a short voice-friendly daily executive briefing that combines:
- today's calendar agenda
- open tasks and due items
- important unread inbox emails

Example voice prompts:
- `Give me my daily executive briefing`
- `What's on my agenda today?`
- `Summarize my day`
