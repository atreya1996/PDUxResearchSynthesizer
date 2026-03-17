---
name: debug
description: "Debug Python errors, tracebacks, SQLite issues, and Gemini/Google API failures in this project. Use when: a script crashes or produces unexpected output, a DB query returns wrong results, an API call fails, or a Streamlit widget behaves incorrectly."
---

# Debug Skill

When invoked, systematically diagnose the reported issue:

1. **Read the traceback** — identify the exact file, line, and exception type
2. **Check env vars** — confirm `.env` is loaded and all required keys are present (`GOOGLE_SERVICE_ACCOUNT_JSON`, `INBOX_FOLDER_ID`, `ARCHIVE_FOLDER_ID`, `GEMINI_API_KEY`)
3. **Inspect the database** — run `sqlite3 research.db ".schema"` and spot-check rows with `SELECT * FROM interviews LIMIT 3`
4. **Trace the data flow** — follow the path: Drive file → download → Gemini upload → response parse → DB insert → Drive archive
5. **Check JSON parsing** — if a `JSONDecodeError` occurs, print the raw Gemini response before `clean_json()` to see what was returned
6. **Retry logic** — confirm `retry_with_backoff` is wrapping the failing call and logging each attempt
7. **Streamlit state** — if a button fires multiple times, check `st.session_state` guards are set BEFORE the API call

Output a root-cause summary and a minimal targeted fix. Do not refactor surrounding code.
