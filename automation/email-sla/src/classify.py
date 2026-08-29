"""Decide whether an inbound message needed a reply from the mailbox owner.

This is where the credits are saved. Classification runs as a cascade:

  1. Deterministic rules -- headers and sender patterns that settle the question
     outright (a List-Unsubscribe header means newsletter, an Auto-Submitted
     header means robot, a DocuSign envelope means a real obligation). These are
     free, offline, and reproducible.
  2. Heuristics -- for ordinary human mail, does it actually ask the owner for
     something? Question marks addressed to the owner, request verbs, being on
     To: rather than Cc:.
  3. An optional LLM pass over whatever is still genuinely ambiguous, which is a
     small fraction of the mailbox. Off unless asked for; see `llm.py`.

Every verdict carries the rule that produced it, so the dashboard can show why a
thread was counted and the owner can argue with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from schema import Message, domain_of

Verdict = Literal["needs_reply", "no_reply", "uncertain"]


@dataclass
class Decision:
    verdict: Verdict
    rule: str
    confidence: float = 1.0

    @property
    def needs_reply(self) -> bool:
        return self.verdict == "needs_reply"


# --------------------------------------------------------------------------
# Layer 1: deterministic rules
# --------------------------------------------------------------------------

# Automated senders that nonetheless create an obligation only the owner can
# discharge -- a click, a signature, an approval. The spec calls these out
# explicitly: they look like noise and are not.
_OBLIGATION_SENDERS = (
    "docusign.net", "docusign.com", "adobesign.com", "echosign.com",
    "hellosign.com", "dropboxsign.com", "pandadoc.com", "signnow.com",
)
_OBLIGATION_SUBJECTS = re.compile(
    r"\b("
    r"signature requested|please sign|awaiting your signature|sign(ing)? request|"
    r"complete with docusign|action required|approval required|approve (this|your|the)|"
    r"access request|requests? access|has invited you|invitation to join|"
    r"invited you to (join|collaborate)|join (the|your) (organi[sz]ation|team|workspace)|"
    r"verify your (identity|entity|organi[sz]ation|domain)|entity verification|"
    r"verification required|confirm your (email|identity|domain)|"
    r"grant(ing)? access|permission request|awaiting (your )?approval|"
    r"pending your (approval|review|action)|requires your (attention|approval|action)"
    r")\b",
    re.IGNORECASE,
)

# Digests and notifications that never need a reply.
_NOISE_SENDERS = (
    "gong.io", "vitally.io", "getvitally.io",
    "noreply@teams.microsoft.com", "no-reply@teams.microsoft.com",
    "calendly.com", "hubspot.com", "mailchimp.com", "sendgrid.net",
    "linkedin.com", "notion.so", "atlassian.net", "statuspage.io",
    "pagerduty.com", "datadoghq.com", "sentry.io", "grafana.com",
)
_NOISE_SUBJECTS = re.compile(
    r"^\s*("
    r"automatic reply|auto(matic)? response|out of office|"
    r"accepted:|aceptado:|declined:|tentative:|cancelled:|canceled:|"
    r"invitation:|updated invitation:|"
    r"your .* digest|weekly digest|daily digest|newsletter|"
    r"missed (activity|conversation)|you have .* unread|"
    r"new booking|booking confirm|meeting (booked|scheduled)|"
    r"\[?monitoring\]?|alert:|incident|deploy(ment)? (succeeded|failed)|"
    r"build (passed|failed|succeeded)"
    r")",
    re.IGNORECASE,
)
_NOREPLY_LOCAL = re.compile(
    r"^(no[-._]?reply|do[-._]?not[-._]?reply|donotreply|notifications?|"
    r"alerts?|mailer[-_]?daemon|postmaster|bounce|automated|system|robot|bot)\b",
    re.IGNORECASE,
)


def _deterministic(msg: Message, me: set[str]) -> Decision | None:
    subject = msg.subject or ""
    sender = msg.sender.addr
    sender_domain = domain_of(sender)

    # Mail the owner sent to themselves from a sequencing tool. The spec warns
    # this shows up looking like real correspondence.
    if sender in me and not (msg.counterparts(me)):
        return Decision("no_reply", "self-addressed automation", 1.0)

    # Obligations win over noise: a DocuSign envelope is automated *and* real.
    if any(sender_domain.endswith(d) for d in _OBLIGATION_SENDERS):
        return Decision("needs_reply", "signature request", 1.0)
    if _OBLIGATION_SUBJECTS.search(subject):
        return Decision("needs_reply", "explicit action/approval request", 0.95)

    # Calendar traffic: Outlook stamps a content class on these.
    if "calendarmessage" in msg.header("content-class").lower():
        return Decision("no_reply", "calendar invite or response", 1.0)

    # Robots announcing themselves.
    auto = msg.header("auto-submitted").lower()
    if auto and auto != "no":
        return Decision("no_reply", f"Auto-Submitted: {auto}", 1.0)
    if msg.header("x-auto-response-suppress"):
        return Decision("no_reply", "auto-response suppressed", 0.9)
    if msg.header("precedence").lower() in {"bulk", "list", "junk"}:
        return Decision("no_reply", "bulk precedence header", 1.0)
    if msg.header("list-unsubscribe") or msg.header("list-id"):
        return Decision("no_reply", "mailing list or newsletter", 1.0)

    if _NOREPLY_LOCAL.match(sender.partition("@")[0]):
        return Decision("no_reply", "no-reply sender", 0.95)
    if any(sender_domain.endswith(d) or sender == d for d in _NOISE_SENDERS):
        return Decision("no_reply", "notification service", 0.95)
    if _NOISE_SUBJECTS.search(subject):
        return Decision("no_reply", "digest, alert or calendar subject", 0.9)

    return None


# --------------------------------------------------------------------------
# Layer 2: heuristics for ordinary human mail
# --------------------------------------------------------------------------

_ASK_RE = re.compile(
    r"\b("
    r"could you|can you|would you|will you|are you able|any chance you|"
    r"please (send|share|confirm|review|approve|advise|let me know|check|look|provide|update)|"
    r"let me know|thoughts\?|any update|any news|following up|circling back|"
    r"gentle reminder|chasing|waiting (on|for) (you|your)|"
    r"need(s|ed)? (your|you to)|require(s|d)? (your|you to)|"
    r"what('s| is) the (status|latest|timeline)|when (can|could|will|do) you|"
    r"who (owns|is handling)|"
    r"over to you|your (thoughts|input|view|steer|call|approval|sign[- ]?off)"
    r")\b",
    re.IGNORECASE,
)
_HANDOVER_RE = re.compile(
    r"\b("
    r"(i|we)('ll| will) (take|handle|pick|sort|action|own|follow)|"
    r"(i|we)('ve| have) (got|taken) (this|it)|leaving (this|it) with|"
    r"handing (this|it) (over |off )?to|assigning (this|it) to|"
    r"(no|nothing) action(ed)? (needed|required)|for your (information|awareness|reference)|"
    r"^fyi\b|just (so you know|keeping you)|no reply (needed|necessary|required)|"
    r"do not reply|copying you (for|in) (visibility|awareness)"
    r")\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?")


def _heuristic(msg: Message, me: set[str]) -> Decision:
    text = f"{msg.subject}\n{msg.body_preview}"
    on_to = any(p.addr in me for p in msg.to)
    only_cc = not on_to and any(p.addr in me for p in msg.cc)

    if _HANDOVER_RE.search(text):
        return Decision("no_reply", "sender states they own the next step", 0.75)

    asks = bool(_ASK_RE.search(text))
    question = bool(_QUESTION_RE.search(text))

    if asks and on_to:
        return Decision("needs_reply", "direct request, owner on To", 0.85)
    if asks and only_cc:
        # A request on a thread the owner is only copied on usually belongs to
        # whoever is on To. Not certain enough to decide alone.
        return Decision("uncertain", "request present but owner only on Cc", 0.5)
    if question and on_to:
        return Decision("needs_reply", "question addressed to owner", 0.7)
    if question and only_cc:
        return Decision("uncertain", "question on a thread owner is Cc'd on", 0.45)
    if on_to and not asks and not question:
        return Decision("uncertain", "addressed to owner, no explicit ask", 0.5)
    return Decision("no_reply", "Cc only, no question or request", 0.7)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def classify(msg: Message, me: set[str]) -> Decision:
    """Classify one inbound message. Sent mail is never classified."""
    if not msg.is_inbound:
        return Decision("no_reply", "outbound", 1.0)
    decision = _deterministic(msg, me)
    if decision is not None:
        return decision
    return _heuristic(msg, me)


def classify_all(messages: list[Message], me: set[str]) -> dict[str, Decision]:
    return {m.uid: classify(m, me) for m in messages if m.is_inbound}


def cascade_stats(decisions: dict[str, Decision]) -> dict[str, int]:
    """How the cascade split the mailbox -- drives the 'what did this cost' note."""
    out = {"needs_reply": 0, "no_reply": 0, "uncertain": 0}
    for d in decisions.values():
        out[d.verdict] += 1
    return out
