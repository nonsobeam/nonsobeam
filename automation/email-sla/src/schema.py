"""The normalised message record every reader produces.

Readers (PST, OLM, mbox) each speak their own dialect; they all hand back
`Message` objects so nothing downstream has to care where the mail came from.
Keep this module free of parsing-library imports so it stays cheap to load.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, Iterator

from workinghours import UTC, to_lisbon

# Reply/forward prefixes, in the languages that turn up in this mailbox plus the
# calendar-response verbs Outlook prepends. Matched case-insensitively, with an
# optional bracketed count ("Re[2]:") and allowed to repeat ("Re: Fw: Re:").
_PREFIXES = [
    "re", "aw", "sv", "vs", "ref",
    "fw", "fwd", "fws", "wg", "tr", "rv", "enc", "encaminhada",
    "accepted", "aceptado", "aceite", "accepté", "angenommen",
    "declined", "rechazado", "recusado", "abgelehnt",
    "tentative", "provisional", "provisorisch",
    "cancelled", "canceled", "cancelado", "annulliert",
    "updated", "actualizado", "atualizado",
]
_PREFIX_RE = re.compile(
    r"^\s*(?:(?:%s)\s*(?:\[\d+\])?\s*:\s*)" % "|".join(_PREFIXES),
    re.IGNORECASE,
)
# Tags mail gateways bolt on, e.g. "[EXTERNAL] ", "[SUSPECTED SPAM]".
_TAG_RE = re.compile(
    r"^\s*[\[\(](?:external|extern|externo|suspected spam|spam|caution|bulk)[^\]\)]*[\]\)]\s*",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


def normalise_subject(subject: str | None) -> str:
    """Strip reply/forward prefixes and gateway tags, casefold, squash spaces.

    This is the coarse half of the threading key -- never use it on its own to
    decide two messages belong together, because subjects like "Quick question"
    collide constantly. `threads.py` pairs it with participant overlap.
    """
    s = subject or ""
    changed = True
    while changed:
        changed = False
        for pattern in (_PREFIX_RE, _TAG_RE):
            s, n = pattern.subn("", s, count=1)
            if n:
                changed = True
    return _WS_RE.sub(" ", s).strip().casefold()


def normalise_address(addr: str | None) -> str:
    """Lowercase an address and drop any display-name wrapper or plus-tag."""
    if not addr:
        return ""
    a = addr.strip()
    if "<" in a and ">" in a:
        a = a[a.rindex("<") + 1 : a.index(">", a.rindex("<"))]
    a = a.strip().strip("'\"").casefold()
    if "@" in a:
        local, _, domain = a.partition("@")
        local = local.partition("+")[0]
        a = f"{local}@{domain}"
    return a


def domain_of(addr: str) -> str:
    return addr.rpartition("@")[2]


@dataclass
class Person:
    name: str = ""
    addr: str = ""

    def __post_init__(self) -> None:
        self.addr = normalise_address(self.addr)
        self.name = (self.name or "").strip()

    @property
    def label(self) -> str:
        return self.name or self.addr or "unknown"


@dataclass
class Message:
    """One email, normalised. Times are stored as timezone-aware UTC."""

    uid: str
    folder: str  # "inbox" or "sent"
    subject: str
    sent_utc: datetime
    sender: Person
    to: list[Person] = field(default_factory=list)
    cc: list[Person] = field(default_factory=list)
    body_preview: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    # Filled in by normalise(), not by readers.
    norm_subject: str = ""

    def __post_init__(self) -> None:
        if self.sent_utc.tzinfo is None:
            self.sent_utc = self.sent_utc.replace(tzinfo=UTC)
        self.sent_utc = self.sent_utc.astimezone(UTC)
        if not self.norm_subject:
            self.norm_subject = normalise_subject(self.subject)
        self.headers = {k.lower(): v for k, v in (self.headers or {}).items()}

    @property
    def sent_lisbon(self) -> datetime:
        return to_lisbon(self.sent_utc)

    @property
    def is_inbound(self) -> bool:
        return self.folder == "inbox"

    def participants(self) -> set[str]:
        """Every address on the message, sender included."""
        out = {self.sender.addr} if self.sender.addr else set()
        out |= {p.addr for p in self.to if p.addr}
        out |= {p.addr for p in self.cc if p.addr}
        return out

    def counterparts(self, me: set[str]) -> set[str]:
        """Participants who are not the mailbox owner."""
        return {a for a in self.participants() if a not in me}

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["sent_utc"] = self.sent_utc.isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Message":
        d = dict(d)
        d["sent_utc"] = datetime.fromisoformat(d["sent_utc"])
        d["sender"] = Person(**d["sender"])
        d["to"] = [Person(**p) for p in d.get("to", [])]
        d["cc"] = [Person(**p) for p in d.get("cc", [])]
        return cls(**d)


def write_jsonl(path: str, messages: Iterable[Message]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for m in messages:
            fh.write(json.dumps(m.to_json(), ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: str) -> Iterator[Message]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield Message.from_json(json.loads(line))
