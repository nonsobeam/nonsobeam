"""Group messages into threads and apply the anchor rule.

`outlook_email_search` never returned a conversationId, and a PST export does
carry one -- but an OLM or mbox export may not, and the spec's matching rule has
to hold either way. So threading is done the robust way: normalised subject
*plus* participant overlap. Subject alone is not enough, because "Quick
question" and "Following up" collide across unrelated accounts.

The anchor rule, restated: for each thread, the anchor is the most recent
inbound message that needed a reply. The clock runs from that message (or from
09:00 the next working day, if it landed out of hours) to the owner's first
reply after it, or to now if there isn't one. One row per thread, always.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from classify import Decision
from schema import Message, domain_of
from workinghours import (
    TARGET_HOURS,
    add_working_hours,
    next_working_start,
    to_lisbon,
    week_start,
    working_hours_between,
)


class _Union:
    """Minimal union-find, for clustering messages that share participants."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def group_threads(messages: Iterable[Message], me: set[str]) -> list[list[Message]]:
    """Cluster messages into threads by normalised subject + participant overlap."""
    by_subject: dict[str, list[Message]] = {}
    for m in messages:
        by_subject.setdefault(m.norm_subject, []).append(m)

    threads: list[list[Message]] = []
    for subject, group in by_subject.items():
        if not subject:
            # No usable subject: never merge these, or every blank-subject mail
            # in eleven months becomes one giant thread.
            threads.extend([m] for m in group)
            continue
        if len(group) == 1:
            threads.append(group)
            continue

        uf = _Union()
        counterparts = [m.counterparts(me) for m in group]
        for i in range(len(group)):
            uf.find(i)
            for j in range(i + 1, len(group)):
                if counterparts[i] & counterparts[j]:
                    uf.union(i, j)

        clusters: dict[int, list[Message]] = {}
        for i, m in enumerate(group):
            clusters.setdefault(uf.find(i), []).append(m)
        threads.extend(clusters.values())

    for t in threads:
        t.sort(key=lambda m: m.sent_utc)
    return threads


@dataclass
class Thread:
    """One conversation, reduced to the single row the report shows."""

    messages: list[Message]
    anchor: Message
    reply: Message | None
    hours: float
    status: str  # replied | breached | awaiting
    rule: str
    account: str = ""
    note: str = ""
    breach_at: datetime | None = None
    _me: set[str] = field(default_factory=set, repr=False)

    @property
    def subject(self) -> str:
        return self.anchor.subject or "(no subject)"

    @property
    def sender(self) -> str:
        return self.anchor.sender.label

    @property
    def sender_addr(self) -> str:
        return self.anchor.sender.addr

    @property
    def received_lisbon(self) -> datetime:
        return self.anchor.sent_lisbon

    @property
    def replied_lisbon(self) -> datetime | None:
        return self.reply.sent_lisbon if self.reply else None

    @property
    def week(self) -> datetime:
        return week_start(self.anchor.sent_utc)

    @property
    def in_target(self) -> bool | None:
        """True/False once replied; None while still open."""
        if self.reply is None:
            return None
        return self.hours <= TARGET_HOURS

    @property
    def is_open(self) -> bool:
        return self.reply is None

    @property
    def breached(self) -> bool:
        return self.hours > TARGET_HOURS

    @property
    def account_label(self) -> str:
        return self.account or "not identified"


def resolve_account(thread_messages: list[Message], me: set[str],
                    accounts: dict[str, str]) -> str:
    """Map a thread to an account by counterpart domain.

    Returns "" when there is no confident match -- the spec is explicit that a
    blank renders as "not identified" and that guessing an account name is out.
    """
    if not accounts:
        return ""
    for m in thread_messages:
        for addr in sorted(m.counterparts(me)):
            name = accounts.get(addr) or accounts.get(domain_of(addr))
            if name:
                return name
    return ""


def build_threads(
    messages: list[Message],
    me: set[str],
    decisions: dict[str, Decision],
    now: datetime,
    accounts: dict[str, str] | None = None,
) -> list[Thread]:
    """Apply the anchor rule to every thread, returning only those needing a reply."""
    accounts = accounts or {}
    out: list[Thread] = []

    for group in group_threads(messages, me):
        needing = [
            m for m in group
            if m.is_inbound and decisions.get(m.uid) and decisions[m.uid].needs_reply
        ]
        if not needing:
            continue

        anchor = needing[-1]  # group is time-sorted, so this is the most recent
        decision = decisions[anchor.uid]

        reply = next(
            (m for m in group if not m.is_inbound and m.sent_utc > anchor.sent_utc),
            None,
        )
        end = reply.sent_utc if reply else now
        hours = working_hours_between(anchor.sent_utc, end)

        if reply is not None:
            status = "replied" if hours <= TARGET_HOURS else "breached"
        else:
            status = "breached" if hours > TARGET_HOURS else "awaiting"

        out.append(
            Thread(
                messages=group,
                anchor=anchor,
                reply=reply,
                hours=hours,
                status=status,
                rule=decision.rule,
                account=resolve_account(group, me, accounts),
                breach_at=add_working_hours(
                    next_working_start(anchor.sent_utc), TARGET_HOURS
                ),
                _me=me,
            )
        )

    out.sort(key=lambda t: t.anchor.sent_utc)
    return out


def due_today(threads: list[Thread], now: datetime) -> list[Thread]:
    """Open threads that cross nine working hours before 18:00 Lisbon today."""
    today = to_lisbon(now).date()
    close = to_lisbon(now).replace(hour=18, minute=0, second=0, microsecond=0)
    out = [
        t for t in threads
        if t.is_open
        and not t.breached
        and t.breach_at is not None
        and t.breach_at.date() == today
        and t.breach_at <= close
    ]
    out.sort(key=lambda t: t.breach_at or now)
    return out


def hours_remaining(thread: Thread) -> float:
    return max(0.0, TARGET_HOURS - thread.hours)
