from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import PrintJob


def _print_payload_type(payload: bytes, file_name: str = "", content_type: str = "") -> str:
    """Return the real printable MIME type, preferring file signature over metadata.

    Older FulfillCore routes sometimes queued PNG label bytes with the historical
    ``application/pdf`` default. The Print Agent should never have to guess around stale
    metadata, so normalize it once when the job is created.
    """
    raw = bytes(payload or b"")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"%PDF"):
        return "application/pdf"
    name = str(file_name or "").strip().lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".pdf"):
        return "application/pdf"
    supplied = str(content_type or "").split(";", 1)[0].strip().lower()
    if supplied in {"image/png", "application/pdf"}:
        return supplied
    return supplied or "application/octet-stream"


def queue_print_job(
    db: Session,
    *,
    station_code: str,
    payload: bytes,
    file_name: str,
    document_type: str,
    client_id: uuid.UUID | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    content_type: str = "application/pdf",
) -> PrintJob:
    code = (station_code or "").strip().upper()
    if not code:
        raise ValueError("station_code is required")
    job = PrintJob(
        client_id=client_id,
        station_code=code,
        document_type=document_type,
        object_type=object_type,
        object_id=object_id,
        file_name=file_name,
        content_type=_print_payload_type(payload, file_name, content_type),
        payload=payload,
        status="QUEUED",
        attempts=0,
    )
    db.add(job)
    db.flush()
    return job
