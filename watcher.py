import io
import json
import logging
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai

import database

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]
POLL_INTERVAL_SECONDS = 60
SCHEMA_PATH = Path(__file__).parent / "schema.json"
TMP_DIR = Path(__file__).parent / "tmp"
TMP_DIR.mkdir(exist_ok=True)


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
            delay = base_delay * (2 ** attempt)
            log.warning("Attempt %d/%d failed (%s). Retrying in %.1fs…", attempt + 1, max_retries, exc, delay)
            time.sleep(delay)
    raise last_exc


# ---------------------------------------------------------------------------
# Google Drive helpers
# ---------------------------------------------------------------------------

def _build_drive_service(config: dict):
    creds = service_account.Credentials.from_service_account_file(
        config["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def list_inbox_files(drive_service, inbox_folder_id: str) -> list:
    query = (
        f"'{inbox_folder_id}' in parents "
        "and trashed = false "
        "and mimeType != 'application/vnd.google-apps.folder'"
    )
    result = retry_with_backoff(
        drive_service.files().list(
            q=query, fields="files(id, name, mimeType)"
        ).execute
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
        drive_service.files().update(
            fileId=file_id,
            addParents=archive_id,
            removeParents=inbox_id,
            fields="id, parents",
        ).execute
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
    log.info("Processing: %s", file_name)

    local_path = download_file(drive_service, file_id, file_name)
    mime_type = detect_mime_type(local_path)

    uploaded = retry_with_backoff(
        gemini_client.files.upload,
        path=str(local_path),
        config={"mime_type": mime_type},
    )

    try:
        response = retry_with_backoff(
            gemini_client.models.generate_content,
            model="gemini-1.5-pro",
            contents=[uploaded, prompt],
        )
        raw = response.text
        data = json.loads(clean_json(raw))
    finally:
        try:
            gemini_client.files.delete(name=uploaded.name)
            log.info("Deleted Gemini file: %s", uploaded.name)
        except Exception as exc:
            log.warning("Could not delete Gemini file %s: %s", uploaded.name, exc)

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
    log.info("Saved interview for: %s", file_name)

    move_to_archive(drive_service, file_id, config["INBOX_FOLDER_ID"], config["ARCHIVE_FOLDER_ID"])
    log.info("Archived Drive file: %s", file_name)

    local_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Watcher loop
# ---------------------------------------------------------------------------

def run_watcher(drive_service, gemini_client, schema: dict, config: dict) -> None:
    prompt = build_prompt(schema)
    log.info("Watcher started. Polling every %ds.", POLL_INTERVAL_SECONDS)
    while True:
        try:
            files = list_inbox_files(drive_service, config["INBOX_FOLDER_ID"])
            log.info("Found %d file(s) in inbox.", len(files))
            for drive_file in files:
                try:
                    process_file(drive_service, drive_file, gemini_client, schema, config, prompt)
                except Exception as exc:
                    log.error("Failed to process %s: %s", drive_file.get("name"), exc, exc_info=True)
        except Exception as exc:
            log.error("Watcher poll error: %s", exc, exc_info=True)
        time.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = _load_env()
    schema = _load_schema()
    database.init_db()

    drive_svc = _build_drive_service(cfg)
    gemini_client = genai.Client(api_key=cfg["GEMINI_API_KEY"])

    run_watcher(drive_svc, gemini_client, schema, cfg)
