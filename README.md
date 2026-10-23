# Typology annotator

Participant-centered annotation of ChatGPT conversations for the project
*Context-Sensitive Accessibility and AI-Mediated Identity Negotiation*.

A participant pastes a ChatGPT share link, reviews the extracted conversation,
approves it, and highlights and annotates passages in Label Studio's annotation
editor. Tasks and annotations live in Label Studio; researchers export a
validated dataset that links every highlight to its exact source message and text.

## Architecture

```
Participant's browser                   This backend (FastAPI)                     Label Studio
─────────────────────                   ──────────────────────                     ────────────
Sign in (study ID + code) ───────────▶  session cookie, rate-limited login
Paste share link ────────────────────▶  validate URL (chatgpt.com/share/<uuid> only)
                                        fetch page (redirects checked) ──▶ chatgpt.com
                                        parse embedded data → messages
Review preview, untick messages ◀────── pending import (not yet in Label Studio)
Confirm ─────────────────────────────▶  normalize + validate ─────────────────────▶ POST /api/tasks/
Annotate in the embedded editor ◀─────  task data, checked against canonical copy ◀── GET task / annotation
Save / Update ───────────────────────▶  validate result against the XML ─────────▶ POST/PATCH annotation
                                        /admin/export ◀──────────────────────────── annotations
```

**Why participants never get Label Studio accounts.** Open-source Label Studio has no
per-task permissions: any account in the organization can list every task in a project
through the UI or API. So participants sign in to this backend only. It holds the Label
Studio API key, creates and reads tasks on their behalf, and checks ownership on every
request. Participants' browsers never contact Label Studio (the end-to-end test checks this).
Researchers use Label Studio normally.

The annotation screen is Label Studio's open-source editor, served by this backend and
configured by [`config/labeling_config.xml`](config/labeling_config.xml), the same XML the
Label Studio project uses (`manage.py sync-config` keeps them identical, and the backend
refuses to create tasks if they differ).

### Where data lives

| Data | Label Studio | Companion DB (`TYPOLOGY_DB`) | Why |
|---|---|---|---|
| Approved conversation (`data.dialogue`) | ✓ task | ✓ canonical record | Canonical copy lets export verify spans against the original text and detects edits in Label Studio |
| Pending import (before approval) | – | ✓ temporary | Deleted on confirm, cancel, or after `PENDING_IMPORT_TTL_HOURS` (default 24) |
| Messages the participant unticked | – | ✓ inside canonical `extracted` | Records the extracted vs. approved mapping (spec §6); delete if your protocol requires |
| Annotation results | ✓ annotation | IDs, version, submitted time | |
| Participants | – | study ID, scrypt hash of access code | |
| Audit log | – | actions and IDs, never content | |

Removing a conversation deletes its Label Studio task (and annotations) and clears the
canonical record.

## Participant workflow

1. Sign in with the study ID and access code from the research team.
2. **Add a conversation**: paste the link from ChatGPT's *Share → Create link*.
3. **Review**: see the title, message count, any extraction warnings, and every message.
   Untick messages to leave out. Nothing reaches Label Studio until **Add to my conversations**.
4. **Annotate**: highlight text in a message. The label (*my text* / *AI's text*) is chosen
   automatically from the message where the highlight starts; a highlight dragged across
   two messages is removed with an explanation. Click a highlight to open its questions.
   All questions are optional.
5. **Submit** (later **Update**). Drafts are also saved automatically every 20 seconds while
   there are changes. The page only says "Saved" after the server has written to Label Studio.
6. Come back any time to edit, delete highlights, or remove the conversation.

If a link can't be read, participants can use their ChatGPT data export instead (read in
their browser; only the chosen conversation is sent, through the same review step).

## Setup

Requires Python 3.11+, Node 18+, and a Label Studio instance (tested with 1.23.2).

```bash
git clone https://github.com/aaleyahlewis/typology_annotator.git
cd typology_annotator
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
```

`npm install` copies the editor into `app/static/lsf`.

### Label Studio

1. Create a project for the study (any title). Note its ID from the URL (`/projects/<ID>`).
2. Create an API key for a researcher service account: *Account & Settings → Personal Access
   Token* (or a legacy token if your organization enables them).
3. Run Label Studio with telemetry and outside error reporting turned off, because tasks
   contain participant conversations:
   ```bash
   export SENTRY_DSN="" FRONTEND_SENTRY_DSN="" COLLECT_ANALYTICS=0
   ```
4. Only give the research team Label Studio accounts.

### Configuration

Set these environment variables for the backend (for example in a `.env` file loaded by
your process manager; never commit it):

| Variable | Required | Meaning |
|---|---|---|
| `SESSION_SECRET` | ✓ | 32+ random characters; signs session cookies |
| `LABEL_STUDIO_URL` | ✓ | e.g. `https://labelstudio.example.edu` |
| `LABEL_STUDIO_API_KEY` | ✓ | Personal Access Token or legacy token |
| `LABEL_STUDIO_PROJECT_ID` | ✓ | the study project's ID |
| `ADMIN_TOKEN` | for HTTP export | bearer token for `/admin/export` |
| `TYPOLOGY_DB` | | SQLite path (default `data/typology.sqlite3`) |
| `EXPORT_DIR` | | where submitted conversations are exported automatically (default `data/exports`; empty turns it off) |
| `SCHEMA_VERSION` | | recorded on every conversation and export (default `1.0`) |
| `HTTPS_ONLY` | | default `1`: cookies only over HTTPS. `0` only with `APP_ENV=test` |
| `SHARE_LINK_BROWSER_FALLBACK` | | `1` to read pages in headless Chromium when they contain no embedded data (needs `pip install -r requirements-browser.txt` and `playwright install chromium`) |
| `IMPORT_RATE_PER_HOUR` | | per participant (default 20) |
| `PENDING_IMPORT_TTL_HOURS` | | unconfirmed imports are discarded after this (default 24) |

Generate a secret with `python3 -c "import secrets; print(secrets.token_hex(32))"`.
The server refuses to start if required settings are missing.

### Run

```bash
.venv/bin/python scripts/manage.py sync-config
.venv/bin/python scripts/manage.py check
.venv/bin/python scripts/manage.py add-participant P01
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Put it behind HTTPS (Caddy, nginx, or your institution's platform). Run a single process
(`--workers 1`); imports run in the background inside that process.

## Researcher commands

```bash
.venv/bin/python scripts/manage.py add-participant P01
.venv/bin/python scripts/manage.py reset-code P01
.venv/bin/python scripts/manage.py disable P01
.venv/bin/python scripts/manage.py enable P01
.venv/bin/python scripts/manage.py sync-config
.venv/bin/python scripts/manage.py check
.venv/bin/python scripts/manage.py export typology_export.json
.venv/bin/python scripts/manage.py expire-imports
```

- `add-participant` prints a one-time access code; only a hash is stored. `reset-code`
  also signs the participant out everywhere.
- `sync-config` copies `config/labeling_config.xml` into the Label Studio project. Run it
  after every XML change; the backend won't create tasks while they differ.
- `check` verifies settings, Label Studio access and that the configs match.
- `export` writes the research export (file permissions 600) and lists conversations
  that need review.

## Research export

### Automatic (after each participant submits)

When a participant clicks **Submit**, or **Update** after editing, the backend writes the
export for that conversation, with no researcher action needed:

```
EXPORT_DIR/
  typology_export.json                  every submitted conversation, rebuilt on each submit
  conversations/P01__conv_….json        one file per conversation (participant__conversation)
```

- Only submitted work is exported; autosaved drafts are not. If the participant edits and
  presses **Update** again, their file and the combined file are rewritten.
- Removing a conversation removes its file and updates the combined file.
- Files are written atomically (never half-written) and readable only by the server's user (600).
- An export problem (for example a full disk) never undoes the participant's save; it's
  logged and recorded in the audit log, and `manage.py export` regenerates everything.
- Put `EXPORT_DIR` on IRB-approved, backed-up storage, or point it at a synced research folder.

### On demand

`manage.py export` or `GET /admin/export` with `Authorization: Bearer $ADMIN_TOKEN`
(see [`app/export.py`](app/export.py), marker `TYPOLOGY_DATA_EXPORT`). One entry per conversation:

```json
{
  "participant_id": "P01",
  "conversation_id": "conv_…",
  "label_studio_task_id": 12,
  "label_studio_annotation_id": 40,
  "title": "Email to my manager",
  "source": "share_link",
  "extraction_method": "structured",
  "extraction_warnings": [{"code": "branches_omitted", "message": "…"}],
  "excluded_source_indexes": [2],
  "schema_version": "1.0",
  "label_config_sha256": "5c84c8…",
  "submitted_at": "2026-10-23T18:02:11+00:00",
  "conversation_context": {"conversation_accessibility_context": "Writing to my manager",
                           "conversation_additional_context": null},
  "annotations": [{
    "annotation_id": 40, "region_id": "6EEy1r0EzM",
    "message_id": "00000000-0000-0000-0000-000000000001", "message_role": "user", "message_index": 0,
    "label": "my text",
    "span": {"start": 36, "end": 59, "text": "neurodivergent 🙂 to get", "start_utf16": 36, "end_utf16": 60},
    "answers": {"user_why": "People at work call me blunt",
                "user_how_strategy": ["Adjusting tone or communication style"],
                "user_accessibility_goal": null, "…": null}
  }],
  "messages": [{"message_id": "…", "index": 0, "source_index": 0, "role": "user", "text": "…"}],
  "validation": {"ok": true, "issues": []}
}
```

- `messages[i]["text"][span.start:span.end] == span.text` in Python (code point positions).
  `start_utf16`/`end_utf16` are Label Studio's own JavaScript positions; they differ after emoji.
- Every question in a highlight's panel is listed; unanswered ones are `null`. Nothing is inferred.
- `validation.issues` flags, per highlight: `message_not_found`, `text_mismatch`,
  `spans_multiple_messages`, `label_role_mismatch`, `answers_without_highlight`,
  `answer_for_other_panel:<field>`. These can only arise if results are edited outside
  this app (e.g. in Label Studio's UI); the app rejects them on save.

## Security and privacy

- **Isolation**: every query is scoped to the signed-in participant; Label Studio task IDs never
  come from the browser. Other participants' conversations return 404 (tested).
- **Sign-in**: scrypt-hashed codes, lockout after 8 failures per study ID (30 per IP) for
  15 minutes, sessions expire after 8 hours and end when a code is reset or the participant
  is disabled.
- **CSRF**: `SameSite=Strict` cookies, JSON-only writes, and cross-origin `Origin` rejected.
- **Share-link fetching**: only `https://chatgpt.com/share/<uuid>` (or `chat.openai.com`);
  every redirect is checked against the same allow-list before it's followed; 25 MB and
  20 s limits; no cookies or logins; an honest User-Agent. The browser fallback blocks all
  requests except chatgpt.com and its asset host.
- **Validation**: conversations are checked before task creation (spec §5), and annotation
  results are checked against the XML before saving: highlights must lie inside one message
  and match its text, labels must match the message's author, answers must belong to that
  highlight's panel and use the XML's options.
- **Integrity**: if a Label Studio task's text no longer matches the canonical record, the
  conversation won't open (logged for the research team).
- **Headers**: strict Content Security Policy (no third-party scripts or connections),
  `X-Frame-Options: DENY`, `no-store` caching, HSTS when `HTTPS_ONLY=1`.
- **Logs** contain IDs and status codes only, never message text or answers.
- **No LLM** is used anywhere in extraction or annotation.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
LS_URL=http://localhost:8080 LS_TOKEN=<legacy token> scripts/run_e2e.sh
```

- **pytest (103 tests)** with an in-memory Label Studio: URL allow-list and redirect blocking,
  both page formats, text preservation (whitespace, code, emoji, Arabic, Chinese), branch and
  hidden-message handling, incomplete-extraction warnings, normalization and validation,
  annotation-result validation, sign-in and lockout, CSRF, participant isolation, duplicate
  and idempotent imports, Label Studio failures and retry without duplicate tasks, save
  conflicts, failed saves not reported as saved, deletion, export validation, automatic export
  (submit vs. draft, rewrite on edit, removal, failure isolation).
- **`scripts/run_e2e.sh` (29 checks)** against a **real Label Studio server**: creates a
  temporary project, serves synthetic share pages locally, and drives a real browser through
  sign-in → paste link → preview → untick a message → confirm → annotate (automatic labels,
  overlapping highlights, cross-message highlight rejected, role-specific questions) →
  submit (automatic export written) → reload → edit (export updated) → second participant
  blocked → export verified. Deletes the
  project afterwards. Use a test Label Studio instance.

All tests use synthetic conversations only.

## Known limitations

- **ChatGPT page format.** chatgpt.com wasn't reachable from the environment this was built
  in, so the extractor was tested against synthetic pages that follow ChatGPT's known
  formats (`__NEXT_DATA__` and React Router "turbo-stream"), not live share links. **Before
  the pilot, import a few real share links of your own** and compare the preview with
  ChatGPT. If ChatGPT changes its pages, the import fails with a clear message (it never
  imports silently wrong data); enable `SHARE_LINK_BROWSER_FALLBACK` or use the export-file
  path while the parser is updated.
- **Automated access to chatgpt.com** may be blocked by ChatGPT or conflict with OpenAI's
  terms. Confirm with your IRB / legal office.
- **Editor version**: the embeddable editor is `label-studio-frontend` 1.7.1 (2023), the last
  standalone build on npm. It reads and writes the same result format as Label Studio 1.23.2
  (verified end to end). To upgrade, build the editor from HumanSignal/label-studio `web/`,
  replace `app/static/lsf`, and re-run the end-to-end test (the chat styling depends on the
  editor's class names).
- Highlighting needs a mouse or touch; there's no keyboard-only text selection yet.
- Written answers must be added with **Add** (or Shift + Enter) before saving; this is how
  Label Studio's TextArea works.
- Single server process (SQLite and in-process background imports).
- Participants reset access codes through the research team (no self-service reset).

## Code markers

`CHATGPT_CONVERSATION_IMPORT` (`app/static/chatgpt_export.js`, `app/static/extract_messages.js`),
`TYPOLOGY_ANNOTATION_VALIDATION` (`app/annotations.py`), `TYPOLOGY_DATA_EXPORT` (`app/export.py`),
and the `TYPOLOGY_*` markers in `config/labeling_config.xml`.
