#!/usr/bin/env python3
"""
SLA reply-time report, computed from Outlook CSV exports.

Companion to ExportMailForSLA.vba. Run that macro in Outlook first; it writes
inbox.csv and sent.csv. Then:

    py sla_from_csv.py

Standard library only — nothing to pip install. Reads the two CSVs, pairs each
inbound message that needed a reply with your first reply in that conversation,
and reports the timings.

Needs no Graph permissions and no admin consent, because the macro already
pulled the data through your signed-in Outlook client.
"""

import argparse
import csv
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

BUSINESS_START = 9
BUSINESS_END = 17

NO_REPLY_HINTS = (
    "no-reply", "noreply", "donotreply", "do-not-reply", "notifications@",
    "mailer-daemon", "postmaster", "bounce", "automated", "alerts@",
)

NEWSLETTER_SUBJECTS = (
    "newsletter", "digest", "weekly update", "unsubscribe", "receipt",
    "invoice", "your order", "password reset", "verification code",
    "out of office", "automatic reply", "undeliverable",
)


def read_csv(path):
    if not os.path.exists(path):
        sys.exit(f"Not found: {path}\nRun the Outlook macro first (see ExportMailForSLA.vba).")
    # The macro writes UTF-16 (VBA's "unicode"); fall back to utf-8 just in case.
    for enc in ("utf-16", "utf-8-sig", "utf-8"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rows = list(csv.DictReader(f))
            if rows and "timestamp" in rows[0]:
                return rows
        except (UnicodeError, UnicodeDecodeError):
            continue
    sys.exit(f"Could not parse {path}. Send me its first two lines and I'll adjust.")


def parse_dt(s):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return None


def detect_me(sent_rows):
    """Whoever sends everything in Sent Items is you."""
    senders = Counter(r["sender"].strip().lower() for r in sent_rows if r.get("sender"))
    if not senders:
        sys.exit("sent.csv has no sender addresses — cannot identify your mailbox.")
    return senders.most_common(1)[0][0]


def needs_reply(row, me):
    sender = (row.get("sender") or "").strip().lower()
    subject = (row.get("subject") or "").strip().lower()

    if not sender or sender == me:
        return False
    if any(h in sender for h in NO_REPLY_HINTS):
        return False
    if any(h in subject for h in NEWSLETTER_SUBJECTS):
        return False

    to = [a for a in (row.get("recipients") or "").lower().split(";") if a]
    return me in to and len(to) <= 5


def business_hours_between(a, b):
    if b <= a:
        return 0.0
    total = 0.0
    cur = a
    while cur < b:
        day_start = cur.replace(hour=BUSINESS_START, minute=0, second=0, microsecond=0)
        day_end = cur.replace(hour=BUSINESS_END, minute=0, second=0, microsecond=0)
        if cur.weekday() < 5:
            ws, we = max(cur, day_start), min(b, day_end)
            if we > ws:
                total += (we - ws).total_seconds() / 3600
        cur = (cur + timedelta(days=1)).replace(
            hour=BUSINESS_START, minute=0, second=0, microsecond=0)
    return total


def pair_replies(inbox, sent, me):
    sent_by_convo = defaultdict(list)
    for r in sent:
        ts = parse_dt(r.get("timestamp"))
        cid = (r.get("conversation_id") or "").strip()
        if ts and cid:
            sent_by_convo[cid].append(ts)
    for k in sent_by_convo:
        sent_by_convo[k].sort()

    rows, unanswered = [], []
    for r in inbox:
        if not needs_reply(r, me):
            continue
        received = parse_dt(r.get("timestamp"))
        if not received:
            continue

        cid = (r.get("conversation_id") or "").strip()
        replies = [t for t in sent_by_convo.get(cid, []) if t > received]

        sender = (r.get("sender") or "").strip()
        subject = (r.get("subject") or "").strip()[:80]

        if not replies:
            unanswered.append({"received": received, "from": sender, "subject": subject})
            continue

        replied = replies[0]
        rows.append({
            "received": received,
            "replied": replied,
            "from": sender,
            "subject": subject,
            "hours": (replied - received).total_seconds() / 3600,
            "business_hours": business_hours_between(received, replied),
        })

    return rows, unanswered


def summarise(rows, unanswered, me):
    if not rows:
        return "No replied messages found. Check that sent.csv covers the same period."

    clock = sorted(r["hours"] for r in rows)
    biz = sorted(r["business_hours"] for r in rows)

    def pct(d, p):
        return d[min(int(len(d) * p / 100), len(d) - 1)]

    total = len(rows) + len(unanswered)
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["received"].strftime("%Y-%m")].append(r["business_hours"])

    L = [
        "",
        "=" * 62,
        f"  EMAIL SLA REPORT  {me}",
        "=" * 62,
        "",
        f"  Emails needing a reply     {total}",
        f"  Replied                    {len(rows)}  ({len(rows)/total*100:.1f}%)",
        f"  Never replied              {len(unanswered)}  ({len(unanswered)/total*100:.1f}%)",
        "",
        "  REPLY TIME — business hours (Mon–Fri 09:00–17:00)",
        f"    Mean       {statistics.mean(biz):>8.1f} h",
        f"    Median     {statistics.median(biz):>8.1f} h",
        f"    75th pct   {pct(biz, 75):>8.1f} h",
        f"    90th pct   {pct(biz, 90):>8.1f} h",
        "",
        "  REPLY TIME — wall clock",
        f"    Mean       {statistics.mean(clock):>8.1f} h  ({statistics.mean(clock)/24:.1f} days)",
        f"    Median     {statistics.median(clock):>8.1f} h",
        "",
        "  WITHIN TARGET",
        f"    < 1 business hour    {sum(1 for h in biz if h <= 1)/len(biz)*100:>5.1f}%",
        f"    < 4 business hours   {sum(1 for h in biz if h <= 4)/len(biz)*100:>5.1f}%",
        f"    < 1 business day     {sum(1 for h in biz if h <= 8)/len(biz)*100:>5.1f}%",
        f"    < 2 business days    {sum(1 for h in biz if h <= 16)/len(biz)*100:>5.1f}%",
        "",
        "  BY MONTH (median business hours)",
    ]
    for m in sorted(by_month):
        v = by_month[m]
        L.append(f"    {m}  {statistics.median(v):>6.1f} h  n={len(v):<4} "
                 + "█" * min(int(statistics.median(v)), 40))

    L += ["", "  SLOWEST REPLIES"]
    for r in sorted(rows, key=lambda r: -r["business_hours"])[:5]:
        L.append(f"    {r['business_hours']:>7.1f} h  {r['from'][:32]:<32} {r['subject'][:40]}")

    L += ["", "=" * 62, ""]
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--inbox", default="inbox.csv")
    p.add_argument("--sent", default="sent.csv")
    p.add_argument("--me", help="your email address (auto-detected if omitted)")
    p.add_argument("--csv", default="sla_detail.csv")
    args = p.parse_args()

    inbox = read_csv(args.inbox)
    sent = read_csv(args.sent)
    me = (args.me or detect_me(sent)).strip().lower()

    print(f"Mailbox: {me}")
    print(f"Read {len(inbox)} inbox and {len(sent)} sent messages.")

    rows, unanswered = pair_replies(inbox, sent, me)
    print(summarise(rows, unanswered, me))

    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["received", "replied", "hours",
                                          "business_hours", "from", "subject"])
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["received"]):
            w.writerow({**r,
                        "received": r["received"].isoformat(),
                        "replied": r["replied"].isoformat(),
                        "hours": f"{r['hours']:.2f}",
                        "business_hours": f"{r['business_hours']:.2f}"})
    print(f"Per-email detail written to {args.csv}\n")


if __name__ == "__main__":
    main()
