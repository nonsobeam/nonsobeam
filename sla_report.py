#!/usr/bin/env python3
"""
SLA reply-time report for Outlook / Microsoft 365 mail.

Runs entirely on your machine. Message bodies never pass through Claude, so
generating the report costs no model tokens.

    pip install msal requests
    python sla_report.py --start 2025-07-01 --end 2026-06-30

Auth uses Entra ID device-code flow, so there is no stored password. You get
a code, you sign in once in a browser, the token is cached locally.

If your tenant blocks the default client, register your own Entra app and:

    export SLA_CLIENT_ID=<your app's Application (client) ID>
    export SLA_TENANT_ID=<your Directory (tenant) ID>   # single-tenant only

"Did this email need a reply?" is the only judgement call in the pipeline.
By default it is answered by a local heuristic (free). Set MULTIVERSE_API_URL
to hand that classification to an external API instead.
"""

import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

try:
    import msal
    import requests
except ImportError:
    sys.exit("Missing deps. Run: pip install msal requests")

# Defaults to Microsoft Graph PowerShell, a public client pre-consented in many
# tenants. Plenty of tenants block it — if yours does, register your own app and
# set SLA_CLIENT_ID, plus SLA_TENANT_ID if the registration is single-tenant.
CLIENT_ID = os.environ.get("SLA_CLIENT_ID", "14d82eec-204b-4c2f-b7e8-296a70dab67e")
AUTHORITY = f"https://login.microsoftonline.com/{os.environ.get('SLA_TENANT_ID', 'organizations')}"
SCOPES = ["Mail.Read", "User.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_CACHE = os.path.expanduser("~/.cache/sla_report_token.json")

# Only these fields are pulled. Bodies are never fetched — smaller, faster,
# and nothing sensitive lands on disk.
SELECT = "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,isDraft"

BUSINESS_START = 9   # 09:00 local
BUSINESS_END = 17    # 17:00 local


# ── auth ────────────────────────────────────────────────

def get_token():
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE):
        cache.deserialize(open(TOKEN_CACHE).read())

    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            sys.exit(f"Device flow failed: {flow.get('error_description')}")
        print(f"\n{flow['message']}\n")
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        sys.exit(f"Auth failed: {result.get('error_description')}")

    if cache.has_state_changed:
        os.makedirs(os.path.dirname(TOKEN_CACHE), exist_ok=True)
        with open(TOKEN_CACHE, "w") as f:
            f.write(cache.serialize())
        os.chmod(TOKEN_CACHE, 0o600)

    return result["access_token"]


# ── graph fetch ────────────────────────────────────────────

def fetch_folder(token, folder, start, end):
    """Page through one mail folder, returning metadata only."""
    headers = {"Authorization": f"Bearer {token}"}
    field = "receivedDateTime" if folder == "inbox" else "sentDateTime"
    url = (
        f"{GRAPH}/me/mailFolders/{folder}/messages"
        f"?$select={SELECT}"
        f"&$filter={field} ge {start.isoformat()}Z and {field} le {end.isoformat()}Z"
        f"&$top=500"
    )

    messages = []
    while url:
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 10))
            print(f"  throttled, waiting {wait}s")
            import time
            time.sleep(wait)
            continue
        r.raise_for_status()
        payload = r.json()
        messages.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink")
        print(f"  {folder}: {len(messages)} messages", end="\r")

    print(f"  {folder}: {len(messages)} messages")
    return messages


def whoami(token):
    r = requests.get(f"{GRAPH}/me?$select=mail,userPrincipalName",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    d = r.json()
    return (d.get("mail") or d.get("userPrincipalName")).lower()


# ── needs-a-reply classification ─────────────────────────────────

NO_REPLY_HINTS = (
    "no-reply", "noreply", "donotreply", "do-not-reply", "notifications@",
    "mailer-daemon", "postmaster", "bounce", "automated", "alerts@",
)

NEWSLETTER_SUBJECTS = (
    "newsletter", "digest", "weekly update", "unsubscribe", "receipt",
    "invoice", "your order", "password reset", "verification code",
    "out of office", "automatic reply", "undeliverable",
)


def needs_reply_heuristic(msg, me):
    """Free, local, deterministic. Good enough for most mailboxes."""
    sender = ((msg.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
    subject = (msg.get("subject") or "").lower()

    if any(h in sender for h in NO_REPLY_HINTS):
        return False
    if any(h in subject for h in NEWSLETTER_SUBJECTS):
        return False
    if sender == me:
        return False

    # Addressed to you directly (not just cc'd / bulk) counts as expecting a reply.
    to = [(r.get("emailAddress") or {}).get("address", "").lower()
          for r in (msg.get("toRecipients") or [])]
    if me in to and len(to) <= 5:
        return True

    return False


def needs_reply_external(messages, me):
    """Batch-classify via an external API.

    Only subject + sender are sent — never message bodies. Set both:
        MULTIVERSE_API_URL   the classification endpoint
        MULTIVERSE_API_KEY   your key (keep it in .env, never commit it)
    """
    url = os.environ.get("MULTIVERSE_API_URL")
    key = os.environ.get("MULTIVERSE_API_KEY")
    if not (url and key):
        return None

    results = {}
    batch_size = 100
    for i in range(0, len(messages), batch_size):
        chunk = messages[i:i + batch_size]
        payload = {
            "recipient": me,
            "messages": [
                {
                    "id": m["id"],
                    "subject": m.get("subject") or "",
                    "from": ((m.get("from") or {}).get("emailAddress") or {}).get("address", ""),
                    "to": [(r.get("emailAddress") or {}).get("address", "")
                           for r in (m.get("toRecipients") or [])],
                }
                for m in chunk
            ],
        }
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        for item in r.json().get("results", []):
            results[item["id"]] = bool(item.get("needs_reply"))
        print(f"  classified {min(i + batch_size, len(messages))}/{len(messages)}", end="\r")

    print()
    return results


# ── SLA maths ─────────────────────────────────────────────

def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def business_hours_between(a, b):
    """Elapsed business hours between two datetimes, Mon–Fri 09:00–17:00."""
    if b <= a:
        return 0.0
    total = 0.0
    cur = a
    while cur < b:
        day_end = cur.replace(hour=BUSINESS_END, minute=0, second=0, microsecond=0)
        day_start = cur.replace(hour=BUSINESS_START, minute=0, second=0, microsecond=0)

        if cur.weekday() < 5:
            window_start = max(cur, day_start)
            window_end = min(b, day_end)
            if window_end > window_start:
                total += (window_end - window_start).total_seconds() / 3600

        cur = (cur + timedelta(days=1)).replace(
            hour=BUSINESS_START, minute=0, second=0, microsecond=0)
    return total


def pair_replies(inbox, sent, me, needs_reply):
    """For each inbound message that needed a reply, find your first reply."""
    sent_by_convo = defaultdict(list)
    for m in sent:
        if m.get("isDraft"):
            continue
        ts = m.get("sentDateTime") or m.get("receivedDateTime")
        if ts:
            sent_by_convo[m.get("conversationId")].append(parse_dt(ts))
    for k in sent_by_convo:
        sent_by_convo[k].sort()

    rows, unanswered = [], []
    for m in inbox:
        if not needs_reply.get(m["id"], False):
            continue
        received = parse_dt(m["receivedDateTime"])
        replies = [t for t in sent_by_convo.get(m.get("conversationId"), []) if t > received]

        sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
        subject = (m.get("subject") or "")[:80]

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


def summarise(rows, unanswered, start, end):
    if not rows:
        return "No replied messages found in range."

    clock = sorted(r["hours"] for r in rows)
    biz = sorted(r["business_hours"] for r in rows)

    def pct(data, p):
        return data[min(int(len(data) * p / 100), len(data) - 1)]

    total_needing = len(rows) + len(unanswered)
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["received"].strftime("%Y-%m")].append(r["business_hours"])

    lines = [
        "",
        "=" * 62,
        f"  EMAIL SLA REPORT  {start:%d %b %Y} – {end:%d %b %Y}",
        "=" * 62,
        "",
        f"  Emails needing a reply     {total_needing}",
        f"  Replied                    {len(rows)}  ({len(rows)/total_needing*100:.1f}%)",
        f"  Never replied              {len(unanswered)}  ({len(unanswered)/total_needing*100:.1f}%)",
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
    for month in sorted(by_month):
        vals = by_month[month]
        bar = "█" * min(int(statistics.median(vals)), 40)
        lines.append(f"    {month}  {statistics.median(vals):>6.1f} h  n={len(vals):<4} {bar}")

    slowest = sorted(rows, key=lambda r: -r["business_hours"])[:5]
    lines += ["", "  SLOWEST REPLIES"]
    for r in slowest:
        lines.append(f"    {r['business_hours']:>7.1f} h  {r['from'][:32]:<32} {r['subject'][:40]}")

    lines += ["", "=" * 62, ""]
    return "\n".join(lines)


# ── main ───────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2025-07-01", help="YYYY-MM-DD")
    p.add_argument("--end", default="2026-06-30", help="YYYY-MM-DD")
    p.add_argument("--csv", default="sla_detail.csv")
    args = p.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc)

    print("Signing in…")
    token = get_token()
    me = whoami(token)
    print(f"Mailbox: {me}\n")

    print("Fetching mail (metadata only)…")
    inbox = fetch_folder(token, "inbox", start, end)
    sent = fetch_folder(token, "sentitems", start, end)

    print("\nClassifying which messages needed a reply…")
    classified = needs_reply_external(inbox, me)
    if classified is None:
        print("  MULTIVERSE_API_URL not set — using local heuristic (free)")
        classified = {m["id"]: needs_reply_heuristic(m, me) for m in inbox}
    else:
        print("  via external classification API")

    rows, unanswered = pair_replies(inbox, sent, me, classified)

    print(summarise(rows, unanswered, start, end))

    with open(args.csv, "w", newline="") as f:
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
