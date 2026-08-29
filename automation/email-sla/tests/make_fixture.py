#!/usr/bin/env python3
"""Generate a synthetic 11-month mailbox for testing.

Entirely invented: fake people, fake ISV accounts, fake domains. No real
customer data goes anywhere near this repository. The point is to exercise the
pipeline over a realistic mix -- genuine client asks, signature requests that
look automated but are real, newsletters, calendar chatter, out-of-office
replies, threads a colleague owns -- and to produce enough volume that the
weekly charts have something to show.

    python3 tests/make_fixture.py --out data/fixture
"""

from __future__ import annotations

import argparse
import os
import random
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

OWNER = ("Thaddeus Uzornne", "thaddeus.uzornne@wetransact.io")

ACCOUNTS = [
    ("Northwind Analytics", "northwind-analytics.example", [
        ("Priya Raghunathan", "priya.raghunathan"),
        ("Milo Ferreira", "milo.ferreira")]),
    ("Cobalt Ledger", "cobaltledger.example", [
        ("Aoife Brennan", "aoife.brennan"),
        ("Tomas Nyberg", "tomas.nyberg")]),
    ("Marisol Robotics", "marisol-robotics.example", [
        ("Dita Kovač", "dita.kovac"),
        ("Ruben Salazar", "ruben.salazar")]),
    ("Halcyon Freight", "halcyonfreight.example", [
        ("Yusuf Adeyemi", "yusuf.adeyemi")]),
    ("Petravox", "petravox.example", [
        ("Ingrid Halvorsen", "ingrid.halvorsen"),
        ("Caio Monteiro", "caio.monteiro")]),
    ("Silverbrook Health", "silverbrookhealth.example", [
        ("Nadia Ostrowska", "nadia.ostrowska")]),
]

COLLEAGUES = [
    ("Bea Lindqvist", "bea.lindqvist@wetransact.io"),
    ("Hugo Marchetti", "hugo.marchetti@wetransact.io"),
    ("Sena Okoro", "sena.okoro@wetransact.io"),
]

# (subject, body, needs_reply)
REAL_ASKS = [
    ("Private offer expiry — can you confirm?",
     "Hi Thaddeus, our private offer looks like it expires Friday. Could you "
     "confirm whether the extension went through? We have procurement waiting.", True),
    ("Question on MACC drawdown",
     "Quick question — does the full marketplace ACV draw down against MACC, or "
     "only the first year? Client is asking and I want to get it right.", True),
    ("Listing rejected in Partner Center",
     "Our listing got rejected again this morning. Can you take a look and let "
     "me know what to change? This is blocking our launch.", True),
    ("Escalation: transactable offer stuck",
     "Escalating this — the offer has been stuck in review for eleven days. We "
     "need your help pushing it through today.", True),
    ("Any update on the co-sell submission?",
     "Following up on the ACE submission from last week. Any news? Our AE is "
     "chasing me.", True),
    ("Approval needed on revised pricing",
     "Please approve the revised pricing tiers so we can publish. Waiting on "
     "your sign-off.", True),
    ("Re: onboarding session next steps",
     "Thanks for the session. Could you send over the technical integration "
     "checklist you mentioned?", True),
    ("Who owns the CSP enablement piece?",
     "Trying to work out who owns CSP enablement on your side — is that you or "
     "someone else? Let me know and I'll direct my questions there.", True),
]

NO_ASK = [
    ("FYI — our Q3 marketing calendar",
     "For your information, attaching our Q3 calendar. No action needed, just "
     "keeping you in the loop.", False),
    ("Notes from yesterday's sync",
     "Sharing notes for visibility. Hugo is taking the follow-up actions on "
     "this one, so nothing needed from you.", False),
    ("Re: integration testing",
     "I'll take this one and come back to the group once we've tested. Leaving "
     "it with me.", False),
]

NOISE = [
    ("Accepted: Quarterly business review", "", "calendar"),
    ("Declined: Pipeline sync", "", "calendar"),
    ("Automatic reply: Out of office", "I am out of the office until Monday.", "ooo"),
    ("Your weekly Gong digest", "Here are your calls this week.", "digest"),
    ("Vitally: 3 accounts changed health score", "Account health summary.", "digest"),
    ("You have 4 unread messages in Teams", "Missed activity summary.", "digest"),
    ("The Marketplace Weekly — issue 44", "This week in cloud marketplaces.", "news"),
    ("New booking: 30-min intro call", "A meeting has been booked.", "booking"),
    ("[Monitoring] API latency returned to normal", "Alert resolved.", "alert"),
]

OBLIGATIONS = [
    ("Signature requested: WeTransact MSA — Petravox",
     "You have been sent a document to review and sign.",
     "dse@docusign.net", "DocuSign NA3 System"),
    ("Action required: entity verification for Cobalt Ledger",
     "Please verify this entity to continue onboarding.",
     "no-reply@partner-verify.example", "Partner Verification"),
    ("Ruben Salazar has invited you to join Marisol Robotics",
     "You have been invited to join an organisation.",
     "invitations@marisol-robotics.example", "Marisol Robotics"),
    ("Access request: production dashboard",
     "A user has requested access and is awaiting your approval.",
     "no-reply@access.example", "Access Control"),
]


def _msg(subject, body, from_pair, to_pairs, cc_pairs, when, extra=None):
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = f"{from_pair[0]} <{from_pair[1]}>"
    m["To"] = ", ".join(f"{n} <{a}>" for n, a in to_pairs)
    if cc_pairs:
        m["Cc"] = ", ".join(f"{n} <{a}>" for n, a in cc_pairs)
    m["Date"] = format_datetime(when)
    m["Message-ID"] = make_msgid(domain="fixture.example")
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content(body or "(no body)")
    return m


def business_moment(rng: random.Random, day: datetime) -> datetime:
    """A plausible send time -- mostly in hours, sometimes not."""
    roll = rng.random()
    if roll < 0.75:
        hour = rng.randint(8, 18)
    elif roll < 0.9:
        hour = rng.choice([6, 7, 19, 20, 21])
    else:
        hour = rng.randint(0, 23)
    return day.replace(hour=hour, minute=rng.randint(0, 59), second=0, microsecond=0)


def build(months: int, seed: int, end: datetime):
    rng = random.Random(seed)
    start = end - timedelta(days=int(months * 30.44))
    inbox, sent = [], []
    thread_no = 0

    day = start
    while day <= end:
        if day.weekday() < 5:
            volume = rng.randint(2, 6)
        else:
            volume = rng.randint(0, 1)

        for _ in range(volume):
            roll = rng.random()
            when = business_moment(rng, day)
            if when > end:
                continue
            thread_no += 1

            if roll < 0.30:
                # A real client ask, usually answered.
                account, domain, people = rng.choice(ACCOUNTS)
                name, local = rng.choice(people)
                subject, body, _ = rng.choice(REAL_ASKS)
                subject = f"{subject} [{thread_no}]"
                sender = (name, f"{local}@{domain}")
                inbox.append(_msg(subject, body, sender, [OWNER], [], when))

                if rng.random() < 0.88:
                    # Reply latency: usually fast, occasionally very slow.
                    if rng.random() < 0.88:
                        delay = timedelta(hours=rng.uniform(0.3, 7))
                    else:
                        delay = timedelta(hours=rng.uniform(20, 80))
                    reply_at = when + delay
                    if reply_at <= end:
                        sent.append(
                            _msg(f"Re: {subject}", "Thanks — here's where we are.",
                                 OWNER, [sender], [], reply_at)
                        )

            elif roll < 0.42:
                # Obligation that looks automated but is real.
                subject, body, addr, name = rng.choice(OBLIGATIONS)
                subject = f"{subject} [{thread_no}]"
                inbox.append(_msg(subject, body, (name, addr), [OWNER], [], when))
                if rng.random() < 0.6:
                    reply_at = when + timedelta(hours=rng.uniform(0.5, 30))
                    if reply_at <= end:
                        sent.append(_msg(f"Re: {subject}", "Done.", OWNER,
                                         [(name, addr)], [], reply_at))

            elif roll < 0.52:
                # A thread a colleague owns.
                account, domain, people = rng.choice(ACCOUNTS)
                name, local = rng.choice(people)
                colleague = rng.choice(COLLEAGUES)
                subject, body, _ = rng.choice(NO_ASK)
                inbox.append(
                    _msg(f"{subject} [{thread_no}]", body, (name, f"{local}@{domain}"),
                         [colleague], [OWNER], when)
                )

            elif roll < 0.62:
                # Cc-only chatter on a live account thread.
                account, domain, people = rng.choice(ACCOUNTS)
                name, local = rng.choice(people)
                colleague = rng.choice(COLLEAGUES)
                inbox.append(
                    _msg(f"Re: weekly sync notes [{thread_no}]",
                         "Sharing the latest deck for awareness.",
                         (name, f"{local}@{domain}"), [colleague], [OWNER], when)
                )

            else:
                # Noise.
                subject, body, kind = rng.choice(NOISE)
                extra = {}
                if kind == "calendar":
                    extra["Content-Class"] = "urn:content-classes:calendarmessage"
                    sender = ("Calendar", "calendar@northwind-analytics.example")
                elif kind == "ooo":
                    extra["Auto-Submitted"] = "auto-replied"
                    sender = ("Aoife Brennan", "aoife.brennan@cobaltledger.example")
                elif kind == "news":
                    extra["List-Unsubscribe"] = "<mailto:unsub@news.example>"
                    extra["Precedence"] = "bulk"
                    sender = ("Marketplace Weekly", "hello@news.example")
                elif kind == "digest":
                    sender = ("Notifications", "no-reply@gong.io")
                elif kind == "booking":
                    sender = ("Calendly", "no-reply@calendly.com")
                else:
                    sender = ("Monitoring", "alerts@statuspage.io")
                inbox.append(
                    _msg(f"{subject} [{thread_no}]", body, sender, [OWNER], [],
                         when, extra)
                )

        day += timedelta(days=1)

    return inbox, sent


def write_mbox(path: str, messages) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        for m in messages:
            fh.write(b"From fixture@example Thu Jan  1 00:00:00 2026\n")
            fh.write(m.as_bytes().replace(b"\nFrom ", b"\n>From "))
            fh.write(b"\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="data/fixture")
    p.add_argument("--months", type=int, default=11)
    p.add_argument("--seed", type=int, default=20260829)
    p.add_argument("--end", default=None, help="ISO end date; defaults to now")
    args = p.parse_args()

    end = (
        datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
        if args.end
        else datetime.now(timezone.utc)
    )
    inbox, sent = build(args.months, args.seed, end)
    write_mbox(os.path.join(args.out, "Inbox.mbox"), inbox)
    write_mbox(os.path.join(args.out, "Sent.mbox"), sent)
    print(f"Wrote {len(inbox)} inbox and {len(sent)} sent messages to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
