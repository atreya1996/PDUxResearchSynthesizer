---
name: frontend-security-coder
description: "Review Streamlit UI and Python code for security vulnerabilities. Use when: adding st.markdown() with user content, processing file uploads, building query strings, handling env vars or credentials, or before any code is committed."
---

# Frontend Security Coder

When invoked, audit the specified file(s) for the following risks:

## Streamlit-Specific
- **XSS via `st.markdown()`** — flag any call using `unsafe_allow_html=True` with non-static content; ensure user-supplied text is sanitised before rendering
- **Query param injection** — validate and sanitise all `st.query_params` values before using them in SQL or file paths
- **File upload handling** — if `st.file_uploader` is used, confirm MIME type is validated server-side and the file is not executed

## Python / SQLite
- **SQL injection** — reject any f-string or `.format()` SQL query; require parameterised queries (`?` placeholders) for all user-controlled values
- **Path traversal** — check that any file path derived from external input is normalised with `Path.resolve()` and confined to an expected directory
- **Credential exposure** — confirm no secrets appear in log statements, error messages, or committed files; `.env` must be in `.gitignore`

## General
- **Dependency pinning** — flag unpinned packages in `requirements.txt` that could introduce supply-chain risk
- **Error message leakage** — ensure tracebacks are not rendered directly to the Streamlit UI in production

Output a severity-ranked list (CRITICAL / HIGH / MEDIUM / LOW) with the exact file:line and a concrete remediation for each finding.
