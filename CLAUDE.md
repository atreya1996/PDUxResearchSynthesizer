# PDUxResearchSynthesizer — Project Memory

## Purpose
Multi-lingual audio/video UX research pipeline for financial inclusion research (PayDay).
Automates: Google Drive polling → Gemini transcription + insight extraction → SQLite storage → Streamlit dashboard.

## Tech Stack
- `google-api-python-client` — Google Drive API (list, download, move files)
- `google-genai` — Gemini 1.5 Pro API (**NOT** `google-generativeai`)
- `streamlit` — Dashboard UI
- `sqlite3` — Local database (built-in)
- `python-dotenv` — Environment variable loading
- `pandas` + `plotly` — Data analysis and charts

## Architecture: Two Separate Processes
```
python watcher.py        # background daemon — polls Drive, runs Gemini, writes to DB
streamlit run app.py     # Streamlit UI — reads DB, triggers re-extraction, runs synthesis
```
**Never mix these two processes.** The infinite polling loop must never run inside the Streamlit event loop.

## Architectural Constraints (non-negotiable)
1. **WAL mode** — every SQLite connection must immediately execute `PRAGMA journal_mode=WAL;`
2. **Retry/backoff** — every Drive API call and every Gemini API call must be wrapped in `retry_with_backoff()`
3. **Gemini file cleanup** — `client.files.delete(name=uploaded.name)` must run in a `finally` block (data privacy)
4. **JSON cleaning** — always run `clean_json()` before `json.loads()` on Gemini responses
5. **Session state guards** — all Gemini-triggering Streamlit buttons must set `st.session_state` flag BEFORE the API call to prevent double-fires
6. **Schema-driven** — field names must come from `schema.json`; never hard-code field names in `watcher.py` or `app.py`

## Schema
Defined in `schema.json`. Fields with `"type": "list"` are stored as JSON-serialized arrays in TEXT columns.

## Environment Variables (see `.env.example`)
- `GOOGLE_SERVICE_ACCOUNT_JSON` — path to service account key file
- `INBOX_FOLDER_ID` — Google Drive folder ID to poll
- `ARCHIVE_FOLDER_ID` — Google Drive folder ID to move processed files into
- `GEMINI_API_KEY` — Gemini API key

## Database
- File: `research.db` (SQLite, WAL mode)
- Table `interviews`: fixed columns + one column per schema field
- Table `syntheses`: stores each Gemini synthesis run with timestamp

## Skills Available in This Project
| Command | Purpose | When to Use |
|---------|---------|-------------|
| `/ui-ux-pro-max` | Design system generation | Before building dashboard UI |
| `/simplify` | Code quality pass | After watcher.py + app.py first draft |
| `/frontend-security-coder` | Security audit | Before every commit |
| `/debug` | Diagnose runtime errors | Whenever a script crashes |
