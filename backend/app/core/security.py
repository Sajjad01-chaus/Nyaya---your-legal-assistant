"""Anonymous sessions and upload validation.

Identity is a signed opaque token in a cookie. No account, no personal data --
enough to own a conversation and a set of uploaded documents, and nothing more.

Ownership is checked on every document read, and a mismatch returns 404 rather
than 403: a 403 confirms the document exists, which is itself a disclosure.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

# Magic bytes, because a filename extension is a claim by the uploader and the
# Content-Type header is a claim by their browser. Neither is evidence.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)

SESSION_COOKIE = "nyaya_session"


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def sign_session(session_id: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{session_id}.{digest}"


def verify_session(token: str, secret: str) -> str | None:
    """Return the session id if the signature holds, else None."""
    if not token or "." not in token:
        return None
    session_id, _, digest = token.rpartition(".")
    expected = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()[:32]
    return session_id if hmac.compare_digest(digest, expected) else None


@dataclass(slots=True)
class UploadVerdict:
    ok: bool
    detected_type: str | None = None
    reason: str = ""


def sniff_mime(head: bytes) -> str | None:
    for signature, mime in _SIGNATURES:
        if head.startswith(signature):
            return mime
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "text/plain"


def validate_upload(
    head: bytes, size: int, *, allowed: frozenset[str], max_bytes: int
) -> UploadVerdict:
    """Reject on real content, with a message that says how to fix it."""
    if size > max_bytes:
        return UploadVerdict(
            False,
            reason=f"File is {size / 1_048_576:.1f} MB; the limit is "
                   f"{max_bytes / 1_048_576:.0f} MB.",
        )
    if size == 0:
        return UploadVerdict(False, reason="The file is empty.")

    detected = sniff_mime(head)
    if detected is None:
        return UploadVerdict(
            False, reason="Unrecognised file type. Upload a PDF, PNG, JPEG or text file."
        )
    if detected not in allowed:
        return UploadVerdict(
            False, detected,
            reason=f"{detected} files are not supported. "
                   f"Supported: {', '.join(sorted(allowed))}.",
        )
    if detected == "application/pdf" and b"/Encrypt" in head:
        return UploadVerdict(
            False, detected,
            reason="This PDF is password-protected. Remove the password and try again.",
        )
    return UploadVerdict(True, detected)


def is_encrypted_pdf(payload: bytes) -> bool:
    """Full-payload check; the header sniff only sees the first few kilobytes."""
    return b"/Encrypt" in payload[:2_000_000]
