import argparse
import importlib.metadata
import inspect
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
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

import database

load_dotenv()

# Log SDK version immediately so every run's artifact is self-contained.
_GENAI_VERSION = importlib.metadata.version("google-genai")

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


def _load_env() -> dict:
    required = [
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "INBOX_FOLDER_ID",
        "ARCHIVE_FOLDER_ID",
        "GEMINI_API_KEY",
    ]
    config = {k: os.environ.get(k) for k in required}
    missing = [k for k, v in config.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing required env vars: {', '.join(missing)}")
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


def retry_with_backoff(func, *args, max_retries: int = 5, base_delay: float = 1.0, **kwargs):
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            delay = base_delay * (2**attempt)
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
# Gemini helpers
# ---------------------------------------------------------------------------


def _validate_gemini_client(client) -> None:
    """Log SDK version and assert the files.upload API is what we expect.

    This runs once at startup and surfaces version mismatches immediately,
    before any file is downloaded from Drive.
    """
    log.info("google-genai SDK version: %s", _GENAI_VERSION)
    sig = inspect.signature(client.files.upload)
    params = list(sig.parameters.keys())
    log.info("files.upload() parameters: %s", params)
    if "file" not in params and "path" not in params:
        raise RuntimeError(
            f"Unrecognised files.upload() signature {sig}. "
            "Pin google-genai to a known version in requirements.txt."
        )


def _gemini_upload(client, local_path: Path, mime_type: str):
    """Upload a local file to Gemini Files API.

    Detects the parameter name at runtime so the code works regardless of
    which google-genai version is installed:
      - >= 1.0:  files.upload(file=..., config={"mime_type": ...})
      - == 0.5:  files.upload(path=..., config={"mime_type": ...})
    """
    sig = inspect.signature(client.files.upload)
    if "file" in sig.parameters:
        return client.files.upload(file=str(local_path), config={"mime_type": mime_type})
    # Fallback: old SDK (google-genai 0.x) used path= instead of file=
    log.warning(
        "Falling back to legacy files.upload(path=) API "
        "(google-genai %s). Upgrade to >=1.0 or pin to ==1.67.0.",
        _GENAI_VERSION,
    )
    return client.files.upload(path=str(local_path), config={"mime_type": mime_type})


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------


def detect_mime_type(file_path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(file_path))
    return mime or "application/octet-stream"


def process_file(
    drive_service,
    drive_file: dict,
    gemini_client,
    schema: dict,
    config: dict,
    prompt: str,
) -> None:
    file_id = drive_file["id"]
    file_name = drive_file["name"]
    log.info("[START] %s", file_name)

    local_path = download_file(drive_service, file_id, file_name)
    size_mb = local_path.stat().st_size / (1024 * 1024)
    mime_type = detect_mime_type(local_path)
    log.info("[DOWNLOADED] %s (%.1f MB, %s)", file_name, size_mb, mime_type)

    uploaded = retry_with_backoff(lambda: _gemini_upload(gemini_client, local_path, mime_type))
    log.info("[UPLOADED TO GEMINI] %s -> %s", file_name, uploaded.name)

    try:
        response = retry_with_backoff(
            lambda: gemini_client.models.generate_content(
                model="gemini-1.5-pro",
                contents=[uploaded, prompt],
            )
        )
        raw = response.text
        data = json.loads(clean_json(raw))
        log.info("[TRANSCRIBED] %s (%d chars)", file_name, len(raw))
    finally:
        try:
            gemini_client.files.delete(name=uploaded.name)
            log.info("[GEMINI CLEANUP] Deleted %s", uploaded.name)
        except Exception as exc:
            log.warning("[GEMINI CLEANUP] Could not delete %s: %s", uploaded.name, exc)

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
        conn.execute(
            f"INSERT INTO interviews ({col_names}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
    log.info("[SAVED TO DB] %s", file_name)

    move_to_archive(drive_service, file_id, config["INBOX_FOLDER_ID"], config["ARCHIVE_FOLDER_ID"])
    log.info("[ARCHIVED] %s", file_name)

    local_path.unlink(missing_ok=True)
    log.info("[DONE] %s", file_name)


# ---------------------------------------------------------------------------
# Watcher loop
# ---------------------------------------------------------------------------


def _write_gha_summary(total: int, succeeded: int, failed: list) -> None:
    """Write a markdown summary to the GitHub Actions job summary page."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [
        "## Watcher Run Summary\n",
        "| | Count |",
        "|---|---|",
        f"| Total files found | {total} |",
        f"| Processed successfully | {succeeded} |",
        f"| Failed | {len(failed)} |",
    ]
    if failed:
        lines.append("\n### Failed files")
        for name, err in failed:
            lines.append(f"- `{name}`: {err}")
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
                    process_file(drive_service, drive_file, gemini_client, schema, config, prompt)
                    succeeded += 1
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

    # Validate SDK contract before touching any files — surfaces mismatches immediately.
    _validate_gemini_client(gemini_client)

    exit_code = run_watcher(drive_svc, gemini_client, schema, cfg, once=args.once)
    raise SystemExit(exit_code)
