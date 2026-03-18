import argparse
import json
import logging
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

import database

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/drive"]
POLL_INTERVAL_SECONDS = 60
SCHEMA_PATH = Path(__file__).parent / "schema.json"
TMP_DIR = Path(__file__).parent / "tmp"
TMP_DIR.mkdir(exist_ok=True)
LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "watcher.log"),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# HttpOptions.timeout is in milliseconds (confirmed: google-genai SDK divides by 1000 internally).
# 600 s = 600 000 ms covers the longest realistic interview clip sent inline.
_GEMINI_TIMEOUT_MS = 600_000


def _load_env() -> dict:
    required = [
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "INBOX_FOLDER_ID",
        "ARCHIVE_FOLDER_ID",
        "GEMINI_API_KEY",
        "DATABASE_URL",
    ]
    config = {k: os.environ.get(k) for k in required}
    missing = [k for k, v in config.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing required env vars: {', '.join(missing)}")
    config["GEMINI_MODEL"] = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    log.info("Using Gemini model: %s", config["GEMINI_MODEL"])
    return config


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def build_prompt(schema: dict) -> str:
    keys = ["full_transcript"] + [f["key"] for f in schema["fields"]]
    list_keys = [f["key"] for f in schema["fields"] if f["type"] == "list"]
    bool_keys = [f["key"] for f in schema["fields"] if f["type"] == "boolean"]
    field_list = "\n".join(f"  - {f['key']}: {f['label']}" for f in schema["fields"])
    return (
        "You are a UX research analyst specialising in financial inclusion.\n"
        "Step 1: Produce a complete verbatim transcript of all speech in the recording.\n"
        "Step 2: Extract the following fields from the transcript.\n\n"
        f"{field_list}\n\n"
        "Return a single JSON object with EXACTLY these keys (no extras):\n"
        f"{json.dumps(keys)}\n\n"
        f"For list fields ({', '.join(list_keys)}) return a JSON array of strings.\n"
        f"For boolean fields ({', '.join(bool_keys)}) return true or false.\n"
        "If a value cannot be determined, return null.\n"
        "Do NOT wrap the JSON in markdown fences."
    )


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------


class DailyQuotaExhaustedError(Exception):
    """Raised when the Gemini daily quota is exhausted.

    The free tier allows 20 requests/day.  Once this is hit there is no point
    retrying any further files in the same watcher run — the quota won't reset
    for ~24 hours.  The watcher loop catches this and aborts immediately.
    """


def _parse_retry_delay(exc: Exception) -> float | None:
    """Extract the suggested retry delay (seconds) from a 429 error message, if present."""
    msg = str(exc)
    m = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _is_daily_quota_error(exc: Exception) -> bool:
    """True when the 429 is a non-recoverable daily/free-tier quota exhaustion.

    Distinguishes from transient per-minute rate limits by inspecting the
    quotaId in the error payload.  Both the quota metric name and the
    message text are checked so this works even if the API response format
    changes slightly between SDK versions.
    """
    msg = str(exc)
    return (
        "PerDay" in msg
        or "per_day" in msg
        or "FreeTier" in msg
        or "free_tier" in msg
        or "GenerateRequestsPerDayPerProject" in msg
    )


def _is_retryable(exc: Exception) -> bool:
    """Return False for errors that retrying can never fix.

    - 429 with daily/free-tier quota exhausted → not retryable (quota resets in ~24h)
    - 429 with per-minute rate limit → retryable (honor retryDelay from API)
    - All other 4xx (400, 401, 403, 404…) → not retryable (config/input errors)
    - 5xx and network errors → retryable
    """
    try:
        from google.genai import errors as _genai_errors

        if isinstance(exc, _genai_errors.ClientError):
            status_code = getattr(exc, "status_code", None)
            # Normalise: SDK may store as int or string
            try:
                status_code = int(status_code)
            except (TypeError, ValueError):
                status_code = 0
            if status_code == 429:
                return not _is_daily_quota_error(exc)
            # All other 4xx are hard errors
            return False
    except ImportError:
        pass
    return True


def retry_with_backoff(func, *args, max_retries: int = 5, base_delay: float = 1.0, **kwargs):
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if _is_daily_quota_error(exc):
                # Re-raise as a special type so the watcher loop can abort
                # all remaining files without attempting downloads.
                raise DailyQuotaExhaustedError(
                    "Gemini daily quota exhausted (free-tier limit reached). "
                    "Remaining files will be skipped. Quota resets in ~24 hours."
                ) from exc
            if not _is_retryable(exc):
                log.error("Non-retryable error — will not retry: %s", exc)
                raise
            suggested = _parse_retry_delay(exc)
            # Cap the suggested delay to 120 s so a misconfigured API doesn't
            # stall the runner for an unreasonable amount of time.
            delay = min(
                max(base_delay * (2**attempt), suggested) if suggested else base_delay * (2**attempt),
                120.0,
            )
            log.warning(
                "Attempt %d/%d failed (%s). Retrying in %.1fs…",
                attempt + 1,
                max_retries,
                exc,
                delay,
            )
            time.sleep(delay)
    raise last_exc


# ---------------------------------------------------------------------------
# Google Drive helpers
# ---------------------------------------------------------------------------


def _build_drive_service(config: dict):
    sa_value = config["GOOGLE_SERVICE_ACCOUNT_JSON"]
    # Support inline JSON string (used in GitHub Actions) or a file path
    try:
        sa_info = json.loads(sa_value)
        creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    except (json.JSONDecodeError, ValueError):
        creds = service_account.Credentials.from_service_account_file(sa_value, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def list_inbox_files(drive_service, inbox_folder_id: str) -> list:
    query = (
        f"'{inbox_folder_id}' in parents "
        "and trashed = false "
        "and mimeType != 'application/vnd.google-apps.folder'"
    )
    result = retry_with_backoff(
        drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute
    )
    return result.get("files", [])


def download_file(drive_service, file_id: str, file_name: str) -> Path:
    local_path = TMP_DIR / file_name
    request = drive_service.files().get_media(fileId=file_id)
    with open(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = retry_with_backoff(downloader.next_chunk)
    return local_path


def move_to_archive(drive_service, file_id: str, inbox_id: str, archive_id: str) -> None:
    retry_with_backoff(
        drive_service.files()
        .update(
            fileId=file_id,
            addParents=archive_id,
            removeParents=inbox_id,
            fields="id, parents",
        )
        .execute
    )


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def clean_json(text: str) -> str:
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"\s*```", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------


# MIME types that Gemini File API can process.
# Anything not on this list will be rejected with an unhelpful error;
# better to catch it here before consuming Drive quota on a download.
_GEMINI_SUPPORTED_MIME_PREFIXES = ("video/", "audio/", "image/", "text/")


def detect_mime_type(file_path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        # Common video extensions that mimetypes misses on some systems
        ext = file_path.suffix.lower()
        mime = {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".avi": "video/x-msvideo",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
            ".m4a": "audio/mp4",
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4v": "video/x-m4v",
        }.get(ext)
    if not mime:
        raise ValueError(
            f"Cannot determine MIME type for '{file_path.name}'. "
            "Rename the file with a standard extension (e.g. .mp4, .mov, .mp3) "
            "or set mimeType explicitly."
        )
    if not any(mime.startswith(p) for p in _GEMINI_SUPPORTED_MIME_PREFIXES):
        raise ValueError(
            f"Unsupported MIME type '{mime}' for '{file_path.name}'. "
            f"Gemini only accepts: {_GEMINI_SUPPORTED_MIME_PREFIXES}"
        )
    return mime


def _upload_to_gemini(gemini_client, local_path: Path, mime_type: str, file_name: str):
    """Upload a file to the Gemini File API and wait for it to become ACTIVE.

    Using the File API instead of inline bytes avoids 500 errors that occur
    when large video payloads are base64-encoded inside the request body.
    Supports files up to 2 GB; Gemini processes the upload asynchronously
    before the generate_content call is made.
    """
    uploaded = retry_with_backoff(
        lambda: gemini_client.files.upload(
            file=local_path,
            config={"mime_type": mime_type, "display_name": file_name},
        )
    )
    # Poll until ACTIVE — video processing is async and typically takes a few
    # seconds, but the backend can return 500 while it is still converting.
    # The loop itself is the retry mechanism; we just catch exceptions inline
    # and continue rather than using retry_with_backoff (which caused cascading
    # double-retries alongside the SDK's own tenacity layer).
    #
    # If files.get() returns 500 persistently (observed in production: Gemini
    # status endpoint degrades while processing is actually completing), we
    # skip the ACTIVE wait after _STUCK_500_THRESHOLD consecutive errors and
    # return the upload handle directly.  generate_content will then either
    # succeed (file was ready) or fail descriptively (file truly not ready).
    _STUCK_500_THRESHOLD = 10  # ~20 s of consecutive 500s → assume possibly ready
    consecutive_errors = 0
    for _ in range(30):
        try:
            file_info = gemini_client.files.get(name=uploaded.name)
            consecutive_errors = 0  # reset on any successful response
        except Exception as poll_exc:  # noqa: BLE001
            consecutive_errors += 1
            log.debug(
                "Transient error polling '%s' state (%d consecutive, will retry): %s",
                file_name, consecutive_errors, poll_exc,
            )
            if consecutive_errors >= _STUCK_500_THRESHOLD:
                log.warning(
                    "[GEMINI] '%s': files.get() has returned errors for ~%ds straight. "
                    "Status endpoint may be degraded — skipping ACTIVE wait and "
                    "attempting generate_content directly.",
                    file_name, consecutive_errors * 2,
                )
                return uploaded
            time.sleep(2)
            continue
        state = file_info.state.name if hasattr(file_info.state, "name") else str(file_info.state)
        if state == "ACTIVE":
            return uploaded
        if state == "FAILED":
            raise RuntimeError(f"Gemini File API processing failed for '{file_name}'")
        time.sleep(2)
    raise TimeoutError(f"Gemini file '{file_name}' did not become ACTIVE within 60 seconds")


def process_file(
    drive_service,
    drive_file: dict,
    gemini_client,
    schema: dict,
    config: dict,
    prompt: str,
    gemini_model: str = DEFAULT_GEMINI_MODEL,
) -> None:
    file_id = drive_file["id"]
    file_name = drive_file["name"]
    log.info("[START] %s", file_name)

    local_path = download_file(drive_service, file_id, file_name)
    size_mb = local_path.stat().st_size / (1024 * 1024)

    # Validate MIME type before upload — fail fast before spending API quota
    # on an unprocessable file.
    mime_type = detect_mime_type(local_path)
    log.info("[DOWNLOADED] %s (%.1f MB, %s)", file_name, size_mb, mime_type)

    data = None
    uploaded_file = None
    try:
        # Upload via the Gemini File API — avoids 500 errors caused by
        # base64-encoding large video payloads inline in the request body.
        # The File API supports up to 2 GB and processes the file async
        # before generate_content is called.
        log.info("[GEMINI] Uploading %s (%.1f MB) via File API…", file_name, size_mb)
        uploaded_file = _upload_to_gemini(gemini_client, local_path, mime_type, file_name)
        log.info("[GEMINI] File ACTIVE — running transcription + extraction…")

        file_part = types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=mime_type)
        response = retry_with_backoff(
            lambda: gemini_client.models.generate_content(
                model=gemini_model,
                contents=[file_part, prompt],
                config={"http_options": {"timeout": _GEMINI_TIMEOUT_MS}},
            )
        )
        raw = response.text if response.text else None
        if not raw:
            # Gemini returned no text — most likely a safety filter block.
            # Raise so the file stays unarchived and the run reports a failure
            # rather than silently writing null data to the DB.
            finish = getattr(response.candidates[0], "finish_reason", "unknown") if response.candidates else "no_candidates"
            raise RuntimeError(
                f"Gemini returned no text for '{file_name}' (finish_reason={finish}). "
                "Check safety filter settings or file content."
            )
        data = json.loads(clean_json(raw))
        log.info("[TRANSCRIBED] %s (%d chars)", file_name, len(raw))
    finally:
        # Always clean up both the local tmp file and the Gemini-hosted file
        # (data privacy — per architectural constraint #3 in CLAUDE.md).
        local_path.unlink(missing_ok=True)
        log.info("[TMP CLEANUP] Removed local file %s", file_name)
        if uploaded_file is not None:
            try:
                gemini_client.files.delete(name=uploaded_file.name)
                log.info("[GEMINI CLEANUP] Deleted uploaded file for %s", file_name)
            except Exception as del_exc:
                log.warning("[GEMINI CLEANUP] Failed to delete uploaded file for %s: %s", file_name, del_exc)

    # Reached only when transcription succeeded (data is not None).
    # Serialize list fields
    for field in schema["fields"]:
        key = field["key"]
        if field["type"] == "list" and isinstance(data.get(key), list):
            data[key] = json.dumps(data[key])

    now = datetime.now(timezone.utc).isoformat()
    columns = ["source_file", "full_transcript", "created_at", "updated_at"] + [
        f["key"] for f in schema["fields"]
    ]
    values = [file_name, data.get("full_transcript"), now, now] + [
        data.get(f["key"]) for f in schema["fields"]
    ]

    placeholders = ", ".join(["%s"] * len(columns))
    col_names = ", ".join(columns)
    with database.get_connection() as conn:
        cur = conn.execute(
            # ON CONFLICT DO NOTHING: if two watcher runs somehow overlap
            # (before the GHA concurrency group queues the second one), the
            # second INSERT is silently skipped instead of creating a duplicate.
            f"INSERT INTO interviews ({col_names}) VALUES ({placeholders}) "
            "ON CONFLICT (source_file) DO NOTHING",
            values,
        )
        conn.commit()
    if cur.rowcount == 0:
        log.warning(
            "[SKIPPED DB INSERT] %s already exists in interviews table — "
            "possible duplicate watcher run. File will still be archived.",
            file_name,
        )
    else:
        log.info("[SAVED TO DB] %s", file_name)

    move_to_archive(drive_service, file_id, config["INBOX_FOLDER_ID"], config["ARCHIVE_FOLDER_ID"])
    log.info("[ARCHIVED] %s → archive", file_name)
    log.info("[DONE] %s", file_name)


# ---------------------------------------------------------------------------
# Watcher loop
# ---------------------------------------------------------------------------


def _write_gha_summary(total: int, succeeded: int, failed: list) -> None:
    """Write a markdown summary to the GitHub Actions job summary page."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    skipped = [(n, e) for n, e in failed if "Skipped" in e]
    errored = [(n, e) for n, e in failed if "Skipped" not in e]
    lines = [
        "## Watcher Run Summary\n",
        "| | Count |",
        "|---|---|",
        f"| Total files found | {total} |",
        f"| Processed successfully | {succeeded} |",
        f"| Errors | {len(errored)} |",
        f"| Skipped (quota) | {len(skipped)} |",
    ]
    if errored:
        lines.append("\n### Errors")
        for name, err in errored:
            lines.append(f"- `{name}`: {err}")
    if skipped:
        lines.append("\n### Skipped (daily quota exhausted)")
        for name, _ in skipped:
            lines.append(f"- `{name}`")
    with open(summary_path, "w") as f:
        f.write("\n".join(lines))


def run_watcher(
    drive_service, gemini_client, schema: dict, config: dict, once: bool = False
) -> int:
    """Returns exit code: 0 if all files processed, 1 if any failed."""
    prompt = build_prompt(schema)
    if once:
        log.info("Watcher running in one-shot mode.")
    else:
        log.info("Watcher started. Polling every %ds.", POLL_INTERVAL_SECONDS)
    while True:
        succeeded = 0
        failed = []
        try:
            files = list_inbox_files(drive_service, config["INBOX_FOLDER_ID"])
            log.info("Found %d file(s) in inbox.", len(files))
            for drive_file in files:
                try:
                    process_file(
                        drive_service,
                        drive_file,
                        gemini_client,
                        schema,
                        config,
                        prompt,
                        gemini_model=config["GEMINI_MODEL"],
                    )
                    succeeded += 1
                except DailyQuotaExhaustedError as exc:
                    log.error(
                        "[QUOTA EXHAUSTED] %s — aborting run. %s",
                        drive_file.get("name"),
                        exc,
                    )
                    failed.append((drive_file.get("name"), str(exc)))
                    # Mark every remaining file as skipped so the summary is accurate.
                    remaining_idx = files.index(drive_file) + 1
                    for skipped_file in files[remaining_idx:]:
                        name = skipped_file.get("name", "(unknown)")
                        log.warning("[SKIPPED] %s — daily quota already exhausted", name)
                        failed.append((name, "Skipped — daily Gemini quota exhausted"))
                    break
                except Exception as exc:
                    log.error("[FAILED] %s: %s", drive_file.get("name"), exc, exc_info=True)
                    failed.append((drive_file.get("name"), str(exc)))
        except Exception as exc:
            log.error("Watcher poll error: %s", exc, exc_info=True)
            failed.append(("(poll error)", str(exc)))

        if once:
            total = succeeded + len(failed)
            log.info("One-shot run complete. Processed: %d, Failed: %d", succeeded, len(failed))
            _write_gha_summary(total, succeeded, failed)
            return 1 if failed else 0
        time.sleep(POLL_INTERVAL_SECONDS)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once", action="store_true", help="Process inbox once and exit (for cron/CI use)"
    )
    args = parser.parse_args()

    cfg = _load_env()
    schema = _load_schema()
    database.init_db()

    drive_svc = _build_drive_service(cfg)
    gemini_client = genai.Client(api_key=cfg["GEMINI_API_KEY"])

    exit_code = run_watcher(drive_svc, gemini_client, schema, cfg, once=args.once)
    raise SystemExit(exit_code)
