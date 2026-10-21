# Typology annotator

A self-contained web app for a participant-centered study of identity negotiation in
AI-mediated accessibility. Participants add one of their own ChatGPT conversations,
highlight passages in their messages and in ChatGPT's replies, and answer typology
questions about each highlight. Researchers export the results as JSON for analysis.

The annotation screen is the open-source Label Studio editor, embedded in this app
and configured by [`config/labeling_config.xml`](config/labeling_config.xml). There is
no Label Studio server and no Label Studio API: this app stores everything itself.

```
Participant browser                                   This app (FastAPI + SQLite)
──────────────────                                    ───────────────────────────
Sign in (study ID + access code)  ───────────────────▶ /api/login
Choose ChatGPT export .zip
  └─ read & converted IN THE BROWSER (chatgpt_export.js)
  └─ participant picks ONE conversation  ────────────▶ /api/tasks        (only that thread is sent)
Annotate in embedded Label Studio editor
  └─ Submit / Update / 30 s autosave  ───────────────▶ /api/tasks/{id}/annotation
                                                      /admin/export  ◀── researcher (admin token)
```

## What participants see

1. **Sign in** with the study ID and access code you gave them.
2. **Your conversations**: they choose their ChatGPT export file (the `.zip` from
   ChatGPT → Settings → Data controls → Export data, or the `conversations.json`
   inside it), search for the conversation, and click **Add this conversation**.
   The file is read on their computer; only the chosen conversation is uploaded.
3. **Annotate**: the conversation appears in a ChatGPT-style layout. They press
   1 (*My words*) or 2 (*AI's words*), highlight text, click the highlight to open
   the matching questions, and press **Submit** (later **Update**).
4. They can reopen, edit, or remove their conversations at any time.

An optional **share-link import** (paste a `chatgpt.com/share/...` link) is included
but **off by default**. See [Share links](#share-links-experimental).

## Run it locally

Requires Python 3.11+ and Node 18+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm install                      # also copies the editor into app/static/lsf

export SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(24))")
.venv/bin/python scripts/manage.py add-participant P01     # prints an access code
.venv/bin/uvicorn app.main:app --port 8765
```

Open http://localhost:8765 and sign in as `P01` with the printed code. A fake
export to try is in [`tests/fixtures/fake_conversations.json`](tests/fixtures/fake_conversations.json).

## Researcher tasks

| Task | Command |
|---|---|
| Add a participant (prints a one-time access code) | `.venv/bin/python scripts/manage.py add-participant P01` |
| Reset a forgotten code | `.venv/bin/python scripts/manage.py reset-code P01` |
| Export everything | `.venv/bin/python scripts/manage.py export typology_export.json` |
| Export over HTTP | `curl -H "Authorization: Bearer $ADMIN_TOKEN" https://your-host/admin/export -o export.json` |

Access codes are stored only as scrypt hashes, so they can't be looked up later.
Send them to participants through your IRB-approved channel.

## Export format

`TYPOLOGY_DATA_EXPORT` in [`app/export.py`](app/export.py). One entry per conversation:

```json
{
  "participant_id": "P01",
  "conversation_id": "68a1f2c3-...",
  "title": "Email to my boss",
  "source": "export_file",
  "codebook_version": "typology-v1",
  "submitted": true,
  "conversation_context": { "conversation_accessibility_context": ["Help writing to my manager"] },
  "spans": [
    {
      "annotation_id": "P-Yob6Jh-W",
      "message_id": "msg-1", "message_index": 0, "role": "user", "label": "My words",
      "selected_text": "I come across as blunt",
      "start_char": 302, "end_char": 324,
      "start_offset_utf16": 302, "end_offset_utf16": 324,
      "label_matches_role": true,
      "answers": {
        "user_why": ["Professional expectations; people call me blunt"],
        "user_how_strategy": ["Adjusting tone or communication style"]
      }
    }
  ],
  "messages": [ { "message_id": "msg-1", "index": 0, "role": "user", "text": "..." } ],
  "label_studio_result": [ "...the editor's raw result, kept for reference..." ]
}
```

- `start_char`/`end_char` are Python string positions: `messages[i]["text"][start_char:end_char]`
  gives the highlighted text. The `*_utf16` values are the editor's own positions
  (they differ only after emoji and other characters outside the Basic Multilingual Plane).
- `label_matches_role` is `false` when a participant chose *My words* for an AI
  message or the reverse. The true role is always in `role`.
- Answer field names match the XML (`user_accessibility_goal`, `ai_role`, ...).
  Unanswered questions are simply absent.

## Project layout

```
app/
  main.py            API, sessions, security headers, pages
  db.py              SQLite schema and queries
  validate.py        checks on uploaded conversations and results (TYPOLOGY_ANNOTATION_VALIDATION)
  export.py          research export (TYPOLOGY_DATA_EXPORT)
  share_link.py      optional server-side share-link reader (off by default)
  static/
    chatgpt_export.js  in-browser ChatGPT export reader (CHATGPT_CONVERSATION_IMPORT)
    extract_messages.js  finds messages on a ChatGPT page (used by share-link import)
    annotate.js        embeds the Label Studio editor and saves results
    conversations.*    add / list / remove conversations
    index.html, login.js, api.js, app.css
config/labeling_config.xml   the annotation interface (TYPOLOGY_* markers inside)
scripts/manage.py            researcher CLI
scripts/copy_editor.cjs      copies the editor build into app/static/lsf on npm install
tools/                       standalone converters (no app needed): Python export converter,
                             validator, and the browser-console share-page script
tests/test_api.py            server tests (pytest)
tests/e2e/flow.cjs           full participant flow in a real browser (Playwright)
```

## Tests

```bash
.venv/bin/python -m pytest -q          # server: auth, isolation, validation, export
scripts/run_e2e.sh                     # starts a throwaway server and runs the browser flow
```

The browser test signs in, uploads an export zip, adds a conversation, checks the
ChatGPT styling, checks that clicking a message creates nothing, makes user, AI and
overlapping highlights, checks the right questions appear for each, answers questions,
submits, reloads, and verifies the export (message IDs, offsets, answers, conversation-level
answers). Screenshots land in `test-results/`.

## Share links (experimental)

`ENABLE_SHARE_LINK_IMPORT=1` adds a "paste a share link" box. The server opens the link in
headless Chromium (`pip install playwright`) and reads the messages with
`app/static/extract_messages.js`. Only `https://chatgpt.com/share/<id>` URLs are accepted.

Treat this as a convenience with a fallback, not the main path:
- ChatGPT may block automated requests from servers, and its page structure changes
  without notice. When that happens participants are told to upload their export instead.
- Automated access may conflict with OpenAI's terms. Check with your IRB / legal office.
- It has been tested only against mock pages, not live chatgpt.com.
- Remind participants to delete the link afterwards (ChatGPT → Settings → Data controls →
  Shared links).

## Deploying for a real study

Before participants use it:

- **HTTPS**: run behind a reverse proxy (Caddy, nginx, or your institution's platform) and
  set `HTTPS_ONLY=1` so session cookies are only sent over HTTPS.
- **Secrets**: set `SESSION_SECRET` (otherwise everyone is signed out on restart) and
  `ADMIN_TOKEN`. Never commit them.
- **Data location**: `TYPOLOGY_DB` points at the SQLite file (default `data/typology.sqlite3`).
  Put it on encrypted, backed-up storage approved by your IRB. SQLite is fine for one server
  and a study-sized number of participants.
- **Retention**: the raw export never reaches the server; only the chosen conversation does.
  Participants can remove a conversation, which deletes its annotations too.
- **No outside calls**: pages load nothing from third-party sites and the Content Security
  Policy blocks requests to other origins.

## Known limitations

- **Editor version**: the embeddable editor is `label-studio-frontend` 1.7.1 (April 2023), the
  last standalone build published to npm. The current editor isn't published separately; to
  upgrade, build it from [HumanSignal/label-studio](https://github.com/HumanSignal/label-studio)
  (`web/`) and replace `app/static/lsf`. Re-run the browser test afterwards, because the chat
  styling in the XML relies on the editor's class names.
- **Label must be re-chosen** before each highlight (press 1 or 2). This is how the editor works.
- **Written answers** must be saved with **Add** (or Shift + Enter) before submitting.
- Highlighting is mouse-based; there's no keyboard-only way to select text yet.
- No password-reset flow for participants: the researcher resets codes with `manage.py`.
- Message text from a share link is the rendered text (no Markdown); from an export it is the
  original text.
