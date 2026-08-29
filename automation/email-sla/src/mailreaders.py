"""Read an Outlook export into normalised `Message` records.

Three formats are supported because "export from Outlook" means different things
on different platforms, and it is not worth a second round trip to find out
which one you got:

  * **PST** -- Outlook for Windows, and mailbox exports from the Microsoft 365
    compliance centre. Needs `libpff-python`.
  * **OLM** -- Outlook for Mac. A zip of XML; stdlib only.
  * **mbox / .eml directory** -- what most third-party exporters emit, and the
    easiest to test against. Stdlib only.

`load()` sniffs the format from the path, so callers just pass the file.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import mailbox
import os
import re
import zipfile
from datetime import datetime, timezone
from email.message import Message as EmailMessage
from typing import Iterator
from xml.etree import ElementTree

from schema import Message, Person, normalise_address

# Header names worth keeping: everything the classifier's deterministic layer
# looks at. Dropping the rest keeps the JSONL small over eleven months.
KEEP_HEADERS = (
    "list-unsubscribe", "list-id", "precedence", "auto-submitted",
    "x-auto-response-suppress", "content-class", "x-ms-exchange-inbox-rules-loop",
    "return-path", "x-mailer", "x-campaign", "x-priority",
)

BODY_PREVIEW_CHARS = 2000
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_body(text: str, is_html: bool) -> str:
    if is_html:
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = _HTML_TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text or "").strip()
    return text[:BODY_PREVIEW_CHARS]


def _people(raw: str | None) -> list[Person]:
    if not raw:
        return []
    out = []
    for name, addr in email.utils.getaddresses([raw]):
        if addr:
            out.append(Person(name=name, addr=addr))
    return out


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _body_of(msg: EmailMessage) -> str:
    """Prefer text/plain; fall back to stripped HTML."""
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() != "text" or part.get_filename():
                continue
            try:
                payload = part.get_content()
            except Exception:
                continue
            if part.get_content_subtype() == "plain" and not plain:
                plain = payload
            elif part.get_content_subtype() == "html" and not html:
                html = payload
    else:
        try:
            payload = msg.get_content()
        except Exception:
            payload = ""
        if msg.get_content_subtype() == "html":
            html = payload
        else:
            plain = payload
    return _clean_body(plain, False) if plain else _clean_body(html, True)


def from_email_message(msg: EmailMessage, folder: str, uid: str) -> Message | None:
    """Convert a parsed RFC-822 message. Returns None if it has no usable date."""
    sent = _parse_date(msg.get("Date"))
    if sent is None:
        return None
    senders = _people(msg.get("From"))
    return Message(
        uid=uid,
        folder=folder,
        subject=msg.get("Subject", "") or "",
        sent_utc=sent,
        sender=senders[0] if senders else Person(),
        to=_people(msg.get("To")),
        cc=_people(msg.get("Cc")),
        body_preview=_body_of(msg),
        headers={h: msg.get(h, "") for h in KEEP_HEADERS if msg.get(h)},
    )


# --------------------------------------------------------------------------
# mbox / .eml
# --------------------------------------------------------------------------


def read_mbox(path: str, folder: str) -> Iterator[Message]:
    box = mailbox.mbox(path, factory=None)
    for i, raw in enumerate(box):
        parsed = email.message_from_bytes(raw.as_bytes(), policy=email.policy.default)
        m = from_email_message(parsed, folder, f"{folder}:mbox:{i}")
        if m:
            yield m


def read_eml_dir(path: str, folder: str) -> Iterator[Message]:
    for root, _, files in os.walk(path):
        for name in sorted(files):
            if not name.lower().endswith((".eml", ".msg.eml")):
                continue
            full = os.path.join(root, name)
            with open(full, "rb") as fh:
                parsed = email.message_from_binary_file(fh, policy=email.policy.default)
            m = from_email_message(parsed, folder, f"{folder}:eml:{name}")
            if m:
                yield m


# --------------------------------------------------------------------------
# PST
# --------------------------------------------------------------------------

_INBOX_NAMES = {"inbox", "caixa de entrada", "bandeja de entrada", "posteingang"}
_SENT_NAMES = {
    "sent items", "sent", "itens enviados", "elementos enviados",
    "gesendete elemente", "éléments envoyés",
}


def _pst_folder_role(name: str) -> str | None:
    n = (name or "").strip().casefold()
    if n in _INBOX_NAMES:
        return "inbox"
    if n in _SENT_NAMES:
        return "sent"
    return None


def _pst_message(entry, folder: str, uid: str) -> Message | None:
    """Build a Message from a pypff message.

    Prefer the RFC-822 transport headers when the PST kept them, since they
    carry List-Unsubscribe, Auto-Submitted and the rest that the classifier
    leans on. Sent items frequently have no headers, so fall back to the MAPI
    properties pypff exposes directly.
    """
    headers = ""
    try:
        headers = entry.get_transport_headers() or ""
    except Exception:
        pass

    if headers.strip():
        parsed = email.message_from_string(headers, policy=email.policy.default)
        sent = _parse_date(parsed.get("Date"))
        senders = _people(parsed.get("From"))
        to, cc = _people(parsed.get("To")), _people(parsed.get("Cc"))
        kept = {h: parsed.get(h, "") for h in KEEP_HEADERS if parsed.get(h)}
        subject = parsed.get("Subject") or entry.get_subject() or ""
    else:
        sent, senders, to, cc, kept = None, [], [], [], {}
        subject = entry.get_subject() or ""

    if sent is None:
        for getter in ("get_client_submit_time", "get_delivery_time",
                       "get_creation_time"):
            try:
                candidate = getattr(entry, getter)()
            except Exception:
                candidate = None
            if candidate:
                sent = (
                    candidate.astimezone(timezone.utc)
                    if candidate.tzinfo
                    else candidate.replace(tzinfo=timezone.utc)
                )
                break
    if sent is None:
        return None

    if not senders:
        try:
            senders = [Person(name=entry.get_sender_name() or "", addr="")]
        except Exception:
            senders = []

    body = ""
    for getter, is_html in (("get_plain_text_body", False), ("get_html_body", True)):
        try:
            raw = getattr(entry, getter)()
        except Exception:
            raw = None
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            body = _clean_body(raw, is_html)
            break

    return Message(
        uid=uid,
        folder=folder,
        subject=subject,
        sent_utc=sent,
        sender=senders[0] if senders else Person(),
        to=to,
        cc=cc,
        body_preview=body,
        headers=kept,
    )


def read_pst(path: str) -> Iterator[Message]:
    try:
        import pypff
    except ImportError as exc:  # pragma: no cover - depends on local install
        raise RuntimeError(
            "Reading a .pst needs libpff-python. Install it with:\n"
            "    pip install libpff-python\n"
            "On Debian/Ubuntu you may first need: sudo apt-get install "
            "build-essential python3-dev"
        ) from exc

    pst = pypff.file()
    pst.open(path)
    try:
        counter = 0

        def walk(folder, inherited: str | None):
            nonlocal counter
            role = _pst_folder_role(folder.get_name()) or inherited
            if role:
                for i in range(folder.get_number_of_sub_messages()):
                    entry = folder.get_sub_message(i)
                    counter += 1
                    m = _pst_message(entry, role, f"{role}:pst:{counter}")
                    if m:
                        yield m
            for i in range(folder.get_number_of_sub_folders()):
                yield from walk(folder.get_sub_folder(i), role)

        yield from walk(pst.get_root_folder(), None)
    finally:
        pst.close()


# --------------------------------------------------------------------------
# OLM (Outlook for Mac)
# --------------------------------------------------------------------------

_OLM_FIELDS = {
    "subject": "OPFMessageCopySubject",
    "sent": "OPFMessageCopySentTime",
    "received": "OPFMessageCopyReceivedTime",
    "body": "OPFMessageCopyBody",
    "html": "OPFMessageCopyHTMLBody",
}


def _olm_addresses(node) -> list[Person]:
    out = []
    for addr in node.iter("emailAddress"):
        a = addr.get("OPFContactEmailAddressAddress") or ""
        n = addr.get("OPFContactEmailAddressName") or ""
        if normalise_address(a):
            out.append(Person(name=n, addr=a))
    return out


def _olm_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw.rstrip("Z"), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return _parse_date(raw)


def read_olm(path: str) -> Iterator[Message]:
    counter = 0
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            lowered = name.casefold()
            if "sent" in lowered:
                folder = "sent"
            elif "inbox" in lowered:
                folder = "inbox"
            else:
                continue
            try:
                tree = ElementTree.fromstring(zf.read(name))
            except ElementTree.ParseError:
                continue
            for node in tree.iter("email"):
                counter += 1

                def text(tag: str) -> str:
                    el = node.find(tag)
                    return (el.text or "") if el is not None else ""

                sent = _olm_time(
                    text(_OLM_FIELDS["sent"]) or text(_OLM_FIELDS["received"])
                )
                if sent is None:
                    continue
                senders = _olm_addresses(node.find("OPFMessageCopyFromAddresses")
                                         or ElementTree.Element("x"))
                body = text(_OLM_FIELDS["body"])
                html = text(_OLM_FIELDS["html"])
                yield Message(
                    uid=f"{folder}:olm:{counter}",
                    folder=folder,
                    subject=text(_OLM_FIELDS["subject"]),
                    sent_utc=sent,
                    sender=senders[0] if senders else Person(),
                    to=_olm_addresses(node.find("OPFMessageCopyToAddresses")
                                      or ElementTree.Element("x")),
                    cc=_olm_addresses(node.find("OPFMessageCopyCCAddresses")
                                      or ElementTree.Element("x")),
                    body_preview=_clean_body(body, False) if body
                    else _clean_body(html, True),
                    headers={},
                )


# --------------------------------------------------------------------------


def load(path: str, folder_hint: str = "inbox") -> list[Message]:
    """Read any supported export. `folder_hint` applies only to mbox/.eml,
    which carry no folder of their own."""
    lower = path.casefold()
    if os.path.isdir(path):
        return list(read_eml_dir(path, folder_hint))
    if lower.endswith(".pst") or lower.endswith(".ost"):
        return list(read_pst(path))
    if lower.endswith(".olm"):
        return list(read_olm(path))
    if lower.endswith((".mbox", ".mbx")) or not os.path.splitext(lower)[1]:
        return list(read_mbox(path, folder_hint))
    raise ValueError(
        f"Unrecognised export format: {path}\n"
        "Expected a .pst, .olm, .mbox, or a directory of .eml files."
    )
