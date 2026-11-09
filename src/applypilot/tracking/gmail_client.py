"""Gmail tracking client supporting both IMAP SSL and MCP.

Modes:
- IMAP SSL (Default / Recommended): Uses standard `imaplib` + Google App Password
  (GMAIL_USER and GMAIL_APP_PASSWORD in ~/.applypilot/.env).
  Zero GCP Cloud Console setup required; fast, native, resilient.
- MCP (Alternative): Uses @gongrzhe/server-gmail-autoauth-mcp over stdio via the `mcp` SDK
  (requires ~/.gmail-mcp/gcp-oauth.keys.json and credentials.json).
"""

import asyncio
import email as email_lib
import html as html_lib
import imaplib
import json
import logging
import os
import quopri
import re
from datetime import datetime, timezone, timedelta
from email.header import decode_header
from email.utils import parseaddr
from pathlib import Path

log = logging.getLogger(__name__)

# MCP credentials directory
GMAIL_MCP_DIR = Path.home() / ".gmail-mcp"
OAUTH_KEYS_PATH = GMAIL_MCP_DIR / "gcp-oauth.keys.json"

# In-memory mapping of email_id -> (imap_uid, folder_name)
_IMAP_MSG_MAP: dict[str, tuple[str, str]] = {}


def get_auth_mode() -> str:
    """Detect whether to use IMAP or MCP.

    Returns:
        'imap', 'mcp', or 'none'.
    """
    from applypilot.config import load_env
    load_env()

    user = os.environ.get("GMAIL_USER") or os.environ.get("GMAIL_EMAIL")
    app_pwd = os.environ.get("GMAIL_APP_PASSWORD") or os.environ.get("GMAIL_PASSWORD")
    if user and app_pwd:
        return "imap"

    creds_path = GMAIL_MCP_DIR / "credentials.json"
    if OAUTH_KEYS_PATH.exists() and creds_path.exists():
        return "mcp"

    return "none"


def check_gmail_setup() -> tuple[bool, str]:
    """Verify Gmail prerequisites are in place (IMAP or MCP).

    Returns:
        (ok, message) — ok is True if setup is valid.
    """
    mode = get_auth_mode()
    if mode == "imap":
        user = os.environ.get("GMAIL_USER") or os.environ.get("GMAIL_EMAIL")
        return True, f"Gmail IMAP configuré ({user})"
    if mode == "mcp":
        return True, "Gmail MCP OAuth configuré."

    return False, (
        "Gmail n'est pas encore configuré pour le suivi des candidatures.\n\n"
        "Configuration rapide (1 minute) :\n"
        "  1. Ouvre https://myaccount.google.com/apppasswords dans ton navigateur\n"
        "  2. Crée un mot de passe d'application nommé 'ApplyPilot'\n"
        "  3. Copie le mot de passe de 16 lettres généré\n"
        "  4. Ajoute ces deux lignes dans ~/.applypilot/.env :\n"
        "       GMAIL_USER=ton.email@gmail.com\n"
        "       GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx\n\n"
        "Une fois ajouté, relance `/track` sur Telegram ou `applypilot track` !"
    )


# ---------------------------------------------------------------------------
# IMAP Engine Implementation
# ---------------------------------------------------------------------------

def _get_imap_client() -> imaplib.IMAP4_SSL:
    """Establish and authenticate an IMAP SSL connection to Gmail."""
    from applypilot.config import load_env
    load_env()

    user = (os.environ.get("GMAIL_USER") or os.environ.get("GMAIL_EMAIL") or "").strip()
    pwd = (os.environ.get("GMAIL_APP_PASSWORD") or os.environ.get("GMAIL_PASSWORD") or "").replace(" ", "").strip()
    if not user or not pwd:
        raise ValueError("GMAIL_USER ou GMAIL_APP_PASSWORD manquant dans ~/.applypilot/.env")

    client = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    client.login(user, pwd)
    return client


def _find_all_mail_folder(client: imaplib.IMAP4_SSL) -> str:
    """Find the Gmail 'All Mail' mailbox (localized in French or English)."""
    try:
        status, folders = client.list()
        if status == "OK" and folders:
            for f in folders:
                f_str = f.decode("utf-8", errors="ignore") if isinstance(f, bytes) else str(f)
                if r"\All" in f_str:
                    parts = f_str.split(' "/" ')
                    if len(parts) == 2:
                        return parts[1].strip()
    except Exception as e:
        log.debug("Could not detect \\All folder via flags: %s", e)

    for candidate in ('"[Gmail]/Tous les messages"', '"[Gmail]/All Mail"', 'INBOX'):
        try:
            status, _ = client.select(candidate, readonly=True)
            if status == "OK":
                return candidate
        except Exception:
            continue
    return "INBOX"


def _decode_mime_header(header_value: str | None) -> str:
    """Decode RFC 2047 encoded MIME header strings."""
    if not header_value:
        return ""
    try:
        decoded_parts = decode_header(header_value)
        out = []
        for content, encoding in decoded_parts:
            if isinstance(content, bytes):
                enc = encoding or "utf-8"
                try:
                    out.append(content.decode(enc, errors="replace"))
                except Exception:
                    out.append(content.decode("utf-8", errors="replace"))
            else:
                out.append(str(content))
        return "".join(out).strip()
    except Exception:
        return str(header_value).strip()


def _parse_email_address(raw_from: str) -> tuple[str, str]:
    """Return (clean_email, display_name)."""
    name, addr = parseaddr(raw_from)
    name = _decode_mime_header(name).strip().strip('"')
    return addr.strip().lower(), name


def _html_to_plain_text(html: str) -> str:
    """Convert HTML email body to readable plain text."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?(p|div|br|tr|li|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    # Remove space before punctuation introduced by tag stripping
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _clean_raw_snippet(raw_bytes: bytes) -> str:
    """Clean MIME headers, decode quoted-printable, strip HTML, and extract readable text."""
    if not raw_bytes:
        return ""
    try:
        lines = raw_bytes.split(b"\n")
        body_parts = []
        in_body = False
        for line in lines:
            line_str = line.strip()
            if not in_body:
                if not line_str:
                    in_body = True
                elif line_str.startswith(b"--") or b"Content-" in line_str or b"MIME" in line_str or b"boundary=" in line_str:
                    continue
            else:
                if line_str.startswith(b"--"):
                    break
                body_parts.append(line)

        payload = b"\n".join(body_parts) if body_parts else raw_bytes
        try:
            decoded = quopri.decodestring(payload).decode("utf-8", errors="replace")
        except Exception:
            decoded = payload.decode("utf-8", errors="replace")

        cleaned = _html_to_plain_text(decoded) if ("<" in decoded or "&" in decoded) else decoded
        cleaned = html_lib.unescape(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:400]
    except Exception as exc:
        log.debug("Snippet clean failed: %s", exc)
        return ""


def _extract_body_and_snippet(msg) -> tuple[str, str]:
    """Extract plain text body and a ~200-char snippet from an EmailMessage."""
    body_plain = []
    body_html = []
    if msg.is_multipart():
        for part in msg.walk():
            cdispo = str(part.get("Content-Disposition") or "")
            if "attachment" in cdispo.lower():
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if ctype == "text/plain":
                    body_plain.append(text)
                elif ctype == "text/html":
                    body_html.append(text)
            except Exception:
                continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    body_html.append(text)
                else:
                    body_plain.append(text)
        except Exception:
            pass

    full_text = "\n".join(body_plain).strip()
    if not full_text and body_html:
        full_text = _html_to_plain_text("\n".join(body_html))

    full_text = full_text[:10000].strip()
    clean_single = re.sub(r"\s+", " ", full_text)
    snippet = clean_single[:200].strip()
    return full_text, snippet


def _imap_verify_connection() -> bool:
    """Test IMAP SSL connectivity."""
    try:
        client = _get_imap_client()
        client.logout()
        return True
    except Exception as e:
        log.error("Gmail IMAP verification failed: %s", e)
        return False


def _imap_search_application_emails(days: int = 14, limit: int = 100) -> list[dict]:
    """Search Gmail via IMAP for application-related emails."""
    client = _get_imap_client()
    results: dict[str, dict] = {}

    try:
        folder = _find_all_mail_folder(client)
        client.select(folder, readonly=True)

        MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        since_str = f"{cutoff.day:02d}-{MONTHS[cutoff.month - 1]}-{cutoff.year}"

        search_queries = [
            f'newer_than:{days}d {{from:noreply OR from:no-reply OR from:notifications OR from:talent OR from:careers OR from:recrutement OR from:rh OR from:greenhouse.io OR from:lever.co OR from:icims.com OR from:myworkdayjobs.com OR from:jobvite.com OR from:smartrecruiters.com OR from:taleo.net OR from:flatchr.io OR from:welcomekit.co OR from:hellowork.com OR from:indeed.com}}',
            f'newer_than:{days}d {{subject:candidature OR subject:application OR subject:entretien OR subject:interview OR subject:stage OR subject:candidat OR subject:candidate OR subject:"suite à votre" OR subject:"next steps"}}',
        ]

        found_uids: set[str] = set()

        # Try Google X-GM-RAW query first
        for q in search_queries:
            try:
                status, data = client.uid('search', None, 'X-GM-RAW', f'"{q}"')
                if status == "OK" and data and data[0]:
                    for u in data[0].split():
                        found_uids.add(u.decode("utf-8", errors="ignore"))
            except Exception as e:
                log.debug("X-GM-RAW search failed: %s", e)
                break

        # Fallback to standard SINCE search if X-GM-RAW returned nothing
        if not found_uids:
            try:
                status, data = client.uid('search', None, f'(SINCE "{since_str}")')
                if status == "OK" and data and data[0]:
                    for u in data[0].split():
                        found_uids.add(u.decode("utf-8", errors="ignore"))
            except Exception as e:
                log.warning("IMAP fallback search failed: %s", e)

        # Process matching UIDs in reverse order (newest first)
        sorted_uids = sorted(list(found_uids), key=lambda x: int(x) if x.isdigit() else 0, reverse=True)

        for uid in sorted_uids[:limit]:
            try:
                # Fetch headers and Gmail message ID
                status, data = client.uid(
                    'fetch', uid,
                    '(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID)] X-GM-MSGID X-GM-THRID BODY.PEEK[TEXT]<0.3000>)'
                )
                if status != "OK" or not data:
                    continue

                raw_header = b""
                raw_snippet = b""
                gm_msgid = ""
                gm_thrid = ""

                for item in data:
                    if isinstance(item, tuple):
                        header_info = item[0].decode("utf-8", errors="ignore")
                        if "HEADER" in header_info:
                            raw_header = item[1]
                        elif "TEXT" in header_info:
                            raw_snippet = item[1]
                        m_id = re.search(r"X-GM-MSGID\s+(\d+)", header_info)
                        if m_id:
                            gm_msgid = format(int(m_id.group(1)), "x")
                        m_th = re.search(r"X-GM-THRID\s+(\d+)", header_info)
                        if m_th:
                            gm_thrid = format(int(m_th.group(1)), "x")

                msg_id = gm_msgid or f"imap_{uid}"
                _IMAP_MSG_MAP[msg_id] = (uid, folder)

                parsed_hdr = email_lib.message_from_bytes(raw_header)
                subject = _decode_mime_header(parsed_hdr.get("Subject", ""))
                sender, sender_name = _parse_email_address(parsed_hdr.get("From", ""))
                to_addr, _ = _parse_email_address(parsed_hdr.get("To", ""))
                date_str = parsed_hdr.get("Date", "")

                # Clean snippet from peek text or fallback to subject
                snippet_text = _clean_raw_snippet(raw_snippet)

                results[msg_id] = {
                    "id": msg_id,
                    "thread_id": gm_thrid or None,
                    "subject": subject,
                    "sender": sender,
                    "sender_name": sender_name,
                    "date": date_str,
                    "snippet": snippet_text or subject,
                    "body": "",
                    "to": to_addr,
                }

                if len(results) >= limit:
                    break

            except Exception as e:
                log.debug("Error parsing IMAP UID %s: %s", uid, e)
                continue

    finally:
        try:
            client.logout()
        except Exception:
            pass

    return list(results.values())


def _imap_read_email_bodies(email_ids: list[str]) -> dict[str, dict]:
    """Batch-read full email bodies for a list of email IDs via IMAP."""
    if not email_ids:
        return {}

    client = _get_imap_client()
    results: dict[str, dict] = {}

    try:
        current_folder = None
        for msg_id in email_ids:
            if msg_id not in _IMAP_MSG_MAP:
                continue
            uid, folder = _IMAP_MSG_MAP[msg_id]
            if folder != current_folder:
                client.select(folder, readonly=True)
                current_folder = folder

            try:
                status, data = client.uid('fetch', uid, '(RFC822)')
                if status != "OK" or not data:
                    continue

                raw_bytes = None
                for item in data:
                    if isinstance(item, tuple) and len(item) == 2:
                        raw_bytes = item[1]
                        break

                if not raw_bytes:
                    continue

                full_msg = email_lib.message_from_bytes(raw_bytes)
                body, snippet = _extract_body_and_snippet(full_msg)
                subject = _decode_mime_header(full_msg.get("Subject", ""))
                sender, sender_name = _parse_email_address(full_msg.get("From", ""))

                results[msg_id] = {
                    "id": msg_id,
                    "thread_id": None,
                    "subject": subject,
                    "sender": sender,
                    "sender_name": sender_name,
                    "date": full_msg.get("Date", ""),
                    "snippet": snippet,
                    "body": body,
                    "to": full_msg.get("To", ""),
                }
            except Exception as e:
                log.warning("Failed to read IMAP body for %s: %s", msg_id, e)

    finally:
        try:
            client.logout()
        except Exception:
            pass

    return results


def _imap_apply_label(email_ids: list[str], label: str = "ap-track") -> int:
    """Apply a label to emails in Gmail via IMAP +X-GM-LABELS."""
    if not email_ids:
        return 0

    client = _get_imap_client()
    labeled = 0

    try:
        current_folder = None
        for msg_id in email_ids:
            if msg_id not in _IMAP_MSG_MAP:
                continue
            uid, folder = _IMAP_MSG_MAP[msg_id]
            if folder != current_folder:
                client.select(folder, readonly=False)
                current_folder = folder

            try:
                status, _ = client.uid('STORE', uid, '+X-GM-LABELS', f'({label})')
                if status == "OK":
                    labeled += 1
            except Exception as e:
                log.debug("IMAP +X-GM-LABELS failed for %s: %s", msg_id, e)

    finally:
        try:
            client.logout()
        except Exception:
            pass

    return labeled


# ---------------------------------------------------------------------------
# MCP Engine Implementation (Fallback if GCP OAuth is configured)
# ---------------------------------------------------------------------------

async def _create_mcp_client():
    """Create an MCP client connected to the Gmail server via stdio."""
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@gongrzhe/server-gmail-autoauth-mcp@1.1.11"],
        env={
            **os.environ,
            "GMAIL_MCP_DIR": str(GMAIL_MCP_DIR),
        },
    )
    return stdio_client(server_params)


async def _call_tool_raw(session, tool_name: str, arguments: dict) -> str:
    """Call an MCP tool and return the raw text response."""
    log.debug("MCP call: %s(%s)", tool_name, json.dumps(arguments)[:200])
    result = await asyncio.wait_for(
        session.call_tool(tool_name, arguments=arguments),
        timeout=120,
    )
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    text = "\n".join(parts)
    return text


try:
    from mcp import ClientSession
except ImportError:
    ClientSession = None


async def _mcp_verify_connection() -> bool:
    """Test Gmail MCP connectivity by listing tools."""
    if ClientSession is None:
        return False
    try:
        async with await _create_mcp_client() as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                tools = await session.list_tools()
                tool_names = [t.name for t in tools.tools]
                return bool(tool_names)
    except Exception as e:
        log.error("Gmail MCP connection failed: %s", e)
        return False


def _parse_search_results(text: str) -> list[dict]:
    """Parse the plain-text search_emails response into email dicts."""
    if text.startswith("Error:"):
        log.warning("Gmail search error: %s", text[:200])
        return []

    emails = []
    blocks = re.split(r'\n\n(?=ID:\s)', text.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        email = {}
        for line in block.split("\n"):
            if line.startswith("ID: "):
                email["id"] = line[4:].strip()
            elif line.startswith("Subject: "):
                email["subject"] = line[9:].strip()
            elif line.startswith("From: "):
                email["from"] = line[6:].strip()
            elif line.startswith("Date: "):
                email["date"] = line[6:].strip()
            elif line.startswith("To: "):
                email["to"] = line[4:].strip()
            elif line.startswith("Snippet: "):
                email["snippet"] = line[9:].strip()

        if email.get("id"):
            emails.append(email)

    return emails


def _parse_read_result(text: str, msg_id: str) -> dict:
    """Parse the plain-text read_email response into an email dict."""
    if text.startswith("Error:"):
        log.warning("Gmail read error for %s: %s", msg_id, text[:200])
        return {"id": msg_id}

    email = {"id": msg_id}
    lines = text.split("\n")
    header_done = False
    body_lines = []

    for line in lines:
        if not header_done:
            if line.startswith("Thread ID: "):
                email["thread_id"] = line[11:].strip()
            elif line.startswith("Subject: "):
                email["subject"] = line[9:].strip()
            elif line.startswith("From: "):
                email["from"] = line[6:].strip()
            elif line.startswith("To: "):
                email["to"] = line[4:].strip()
            elif line.startswith("Date: "):
                email["date"] = line[6:].strip()
            elif line == "" and email.get("subject"):
                header_done = True
        else:
            body_lines.append(line)

    email["body"] = "\n".join(body_lines).strip()[:10000]
    return email


def _normalize_email(raw: dict) -> dict:
    """Normalize a parsed email dict into the standard format."""
    sender_raw = raw.get("from") or ""
    sender = sender_raw
    sender_name = ""
    if "<" in sender_raw:
        parts = sender_raw.split("<", 1)
        sender_name = parts[0].strip().strip('"')
        sender = parts[1].rstrip(">").strip()

    return {
        "id": raw.get("id", ""),
        "thread_id": raw.get("thread_id"),
        "subject": raw.get("subject") or "",
        "sender": sender,
        "sender_name": sender_name,
        "date": raw.get("date") or "",
        "snippet": raw.get("snippet") or "",
        "body": raw.get("body") or "",
        "to": raw.get("to") or "",
    }


async def _mcp_search_application_emails(days: int = 14, limit: int = 100) -> list[dict]:
    """Search Gmail for application-related emails via MCP."""
    if ClientSession is None:
        raise RuntimeError("mcp package is not installed.")

    base = f"newer_than:{days}d"
    search_queries = [
        f"{base} {{from:noreply OR from:no-reply OR from:notifications OR from:talent OR from:careers}}",
        f"{base} {{from:greenhouse.io OR from:lever.co OR from:icims.com OR from:myworkdayjobs.com OR from:jobvite.com OR from:smartrecruiters.com OR from:taleo.net OR from:flatchr.io OR from:welcomekit.co OR from:hellowork.com OR from:indeed.com}}",
        f"{base} {{subject:candidature OR subject:application OR subject:entretien OR subject:interview OR subject:candidat OR subject:candidate OR subject:\"next steps\"}}",
        f"in:spam {base} {{subject:candidature OR subject:application OR subject:interview OR subject:entretien}}",
    ]

    all_emails: dict[str, dict] = {}

    try:
        async with await _create_mcp_client() as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)

                for query in search_queries:
                    try:
                        raw_text = await _call_tool_raw(
                            session, "search_emails",
                            {"query": query, "maxResults": limit},
                        )
                    except asyncio.TimeoutError:
                        log.warning("Gmail search timed out: %s", query[:60])
                        continue
                    except Exception as e:
                        log.warning("Gmail search failed: %s", e)
                        continue

                    results = _parse_search_results(raw_text)
                    for msg in results:
                        msg_id = msg.get("id")
                        if not msg_id or msg_id in all_emails:
                            continue
                        all_emails[msg_id] = _normalize_email(msg)
                        if len(all_emails) >= limit:
                            break

    except Exception as e:
        log.error("Gmail MCP session failed: %s", e)
        raise

    return list(all_emails.values())


async def _mcp_read_email_bodies(email_ids: list[str]) -> dict[str, dict]:
    """Batch-read full email bodies for a list of email IDs via MCP."""
    if not email_ids or ClientSession is None:
        return {}

    results: dict[str, dict] = {}
    try:
        async with await _create_mcp_client() as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                for msg_id in email_ids:
                    try:
                        full_text = await _call_tool_raw(
                            session, "read_email",
                            {"messageId": msg_id},
                        )
                        full = _parse_read_result(full_text, msg_id)
                        results[msg_id] = _normalize_email(full)
                    except Exception as e:
                        log.debug("Could not read email %s: %s", msg_id, e)
    except Exception as e:
        log.error("Gmail MCP session failed during body read: %s", e)
        raise

    return results


async def _mcp_apply_label(email_ids: list[str], label: str = "ap-track") -> int:
    """Apply a label via MCP."""
    if not email_ids or ClientSession is None:
        return 0

    try:
        async with await _create_mcp_client() as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                raw = await _call_tool_raw(session, "get_or_create_label", {"name": label})
                m = re.search(r"Label_\w+", raw)
                if not m:
                    return 0
                label_id = m.group(0)

                CHUNK = 50
                total_labeled = 0
                for i in range(0, len(email_ids), CHUNK):
                    chunk = email_ids[i:i + CHUNK]
                    raw = await _call_tool_raw(session, "batch_modify_emails", {
                        "messageIds": chunk,
                        "addLabelIds": [label_id],
                    })
                    if not raw.startswith("Error:"):
                        total_labeled += len(chunk)
                return total_labeled
    except Exception as e:
        log.warning("Gmail label session failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Public Unified API
# ---------------------------------------------------------------------------

async def verify_connection() -> bool:
    """Test Gmail connectivity (auto-detects IMAP or MCP).

    Returns True if authentication succeeds.
    """
    mode = get_auth_mode()
    if mode == "imap":
        return await asyncio.to_thread(_imap_verify_connection)
    elif mode == "mcp":
        return await _mcp_verify_connection()
    return False


async def search_application_emails(days: int = 14, limit: int = 100) -> list[dict]:
    """Search Gmail for application-related emails, returning metadata only.

    Supports both IMAP SSL and MCP.

    Args:
        days: Look-back period in days.
        limit: Maximum emails to return.

    Returns:
        List of normalized email dicts with subject, sender, date, snippet.
    """
    mode = get_auth_mode()
    if mode == "imap":
        return await asyncio.to_thread(_imap_search_application_emails, days, limit)
    elif mode == "mcp":
        return await _mcp_search_application_emails(days, limit)
    else:
        ok, msg = check_gmail_setup()
        raise RuntimeError(msg)


async def read_email_bodies(email_ids: list[str]) -> dict[str, dict]:
    """Batch-read full email bodies for a list of email IDs.

    Args:
        email_ids: List of Gmail message IDs to read.

    Returns:
        Dict mapping email ID to normalized email dict (with body).
    """
    if not email_ids:
        return {}

    mode = get_auth_mode()
    if mode == "imap":
        return await asyncio.to_thread(_imap_read_email_bodies, email_ids)
    elif mode == "mcp":
        return await _mcp_read_email_bodies(email_ids)
    return {}


async def apply_label_to_emails(email_ids: list[str], label: str = "ap-track") -> int:
    """Apply a Gmail label to a batch of email IDs.

    Args:
        email_ids: Gmail message IDs to label.
        label: Label name to apply.

    Returns:
        Count of emails labeled.
    """
    if not email_ids:
        return 0

    mode = get_auth_mode()
    if mode == "imap":
        return await asyncio.to_thread(_imap_apply_label, email_ids, label)
    elif mode == "mcp":
        return await _mcp_apply_label(email_ids, label)
    return 0


async def fetch_application_emails(days: int = 14, limit: int = 100) -> list[dict]:
    """Fetch application-related emails from Gmail with body text."""
    emails = await search_application_emails(days=days, limit=limit)
    if not emails:
        return emails

    ids = [e["id"] for e in emails]
    bodies = await read_email_bodies(ids)

    for email in emails:
        if email["id"] in bodies:
            full = bodies[email["id"]]
            email["body"] = full.get("body", "")
            email["thread_id"] = full.get("thread_id") or email.get("thread_id")

    return emails
