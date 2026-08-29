#!/usr/bin/env python3
"""Email SLA scan -- one-shot, local, over an Outlook export.

Reads a local export of Inbox and Sent Items, works out which inbound messages
needed a reply, applies the anchor rule per thread, computes working-hours SLA
figures, and writes a self-contained HTML dashboard.

No mailbox is contacted, no mail is sent, and nothing is written anywhere except
the output file. By default no model is called either -- classification is done
by rule, offline and free. See `--llm` if you want a model to arbitrate the
ambiguous tail.

Examples
--------
    # PST or OLM: one file holds both folders
    python3 run.py --export ~/exports/mailbox.pst

    # mbox or .eml directories: one per folder
    python3 run.py --inbox ~/exports/Inbox.mbox --sent ~/exports/Sent.mbox

    # confirm an LLM endpoint and key before spending anything on it
    python3 run.py --probe-llm
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from classify import cascade_stats, classify_all  # noqa: E402
from config import Config  # noqa: E402
from mailreaders import load  # noqa: E402
from render import render  # noqa: E402
from schema import Message, write_jsonl  # noqa: E402
from sla import ReconciliationError, build_report  # noqa: E402
from threads import build_threads  # noqa: E402
from workinghours import LISBON, UTC, to_lisbon  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load_dotenv(path: str) -> None:
    """Minimal .env loader, so the API key never has to live in a config file."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="One-shot local Email SLA scan over an Outlook export.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--export", help="A .pst or .olm holding both Inbox and Sent Items")
    p.add_argument("--inbox", help="Inbox as .mbox or a directory of .eml")
    p.add_argument("--sent", help="Sent Items as .mbox or a directory of .eml")
    p.add_argument("--config", default=None, help="Path to config.json")
    p.add_argument("--out", default=os.path.join(HERE, "out", "sla-dashboard.html"))
    p.add_argument("--months", type=int, default=None,
                   help="Window length in months (default: config, else 11)")
    p.add_argument("--now", default=None,
                   help="Override 'now' as ISO-8601, for reproducible runs")
    p.add_argument("--llm", action="store_true",
                   help="Send only the ambiguous messages to an LLM. Off by default.")
    p.add_argument("--probe-llm", action="store_true",
                   help="List models at the configured endpoint, then exit")
    p.add_argument("--dump", default=None,
                   help="Also write the parsed messages to this JSONL path")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def resolve_now(raw: str | None) -> datetime:
    if raw:
        dt = datetime.fromisoformat(raw)
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=LISBON).astimezone(UTC)
    return datetime.now(UTC)


def read_messages(args: argparse.Namespace, say) -> list[Message]:
    messages: list[Message] = []
    if args.export:
        say(f"Reading {args.export} …")
        messages = load(args.export)
    else:
        if args.inbox:
            say(f"Reading inbox from {args.inbox} …")
            messages += load(args.inbox, "inbox")
        if args.sent:
            say(f"Reading sent items from {args.sent} …")
            messages += load(args.sent, "sent")
    return messages


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(os.path.join(HERE, ".env"))
    say = (lambda *a: None) if args.quiet else (lambda *a: print(*a))

    if args.probe_llm:
        from llm import LLMConfig, LLMError, probe

        try:
            cfg = LLMConfig.from_env()
            say(f"Probing {cfg.base_url} …")
            models = probe(cfg)
        except LLMError as exc:
            print(f"LLM probe failed: {exc}", file=sys.stderr)
            return 2
        say(f"Endpoint reachable, key accepted. {len(models)} model(s):")
        for m in models:
            say(f"  {m}")
        return 0

    if not (args.export or args.inbox or args.sent):
        print(
            "Nothing to read. Pass --export for a .pst/.olm, or --inbox/--sent "
            "for mbox or .eml. See --help.",
            file=sys.stderr,
        )
        return 2

    config = Config.load(args.config)
    config.validate()
    months = args.months or config.months

    now = resolve_now(args.now)
    window_start = now - timedelta(days=int(months * 30.44))
    say(
        f"Now: {to_lisbon(now):%a %d %b %Y %H:%M} Lisbon · "
        f"window {to_lisbon(window_start):%d %b %Y} → {to_lisbon(now):%d %b %Y}"
    )

    messages = read_messages(args, say)
    if not messages:
        print("No messages parsed from the export. Check the path and format.",
              file=sys.stderr)
        return 1

    before = len(messages)
    messages = [m for m in messages if window_start <= m.sent_utc <= now]
    counts = {
        "inbox": sum(1 for m in messages if m.is_inbound),
        "sent": sum(1 for m in messages if not m.is_inbound),
    }
    say(
        f"Parsed {before} messages, {len(messages)} inside the window "
        f"({counts['inbox']} inbox, {counts['sent']} sent)."
    )
    if not counts["sent"]:
        say(
            "  Warning: no sent items in range. Without them every thread looks "
            "unanswered, so the figures will be wrong. Export Sent Items too."
        )

    if args.dump:
        os.makedirs(os.path.dirname(os.path.abspath(args.dump)) or ".", exist_ok=True)
        write_jsonl(args.dump, messages)
        say(f"Wrote parsed messages to {args.dump}")

    say("Classifying …")
    decisions = classify_all(messages, config.me)
    cascade = cascade_stats(decisions)
    say(
        f"  rules: {cascade['needs_reply']} need a reply, "
        f"{cascade['no_reply']} do not, {cascade['uncertain']} uncertain."
    )

    llm_used = False
    if args.llm and cascade["uncertain"]:
        from llm import LLMError, resolve_uncertain

        uncertain = [
            m for m in messages
            if m.is_inbound and decisions.get(m.uid)
            and decisions[m.uid].verdict == "uncertain"
        ]
        try:
            say(f"Sending {len(uncertain)} ambiguous messages to the model …")
            decisions.update(resolve_uncertain(uncertain, verbose=not args.quiet))
            llm_used = True
            cascade = cascade_stats(decisions)
        except LLMError as exc:
            say(f"  LLM pass failed, keeping the offline verdicts: {exc}")

    # An unresolved "uncertain" is counted as needing a reply: a missed
    # obligation costs more than an extra row to dismiss by hand.
    for uid, decision in decisions.items():
        if decision.verdict == "uncertain":
            decision.verdict = "needs_reply"
            decision.rule += " (unresolved, counted in)"

    threads = build_threads(messages, config.me, decisions, now, config.accounts)
    say(f"Built {len(threads)} threads needing a reply.")

    try:
        report = build_report(threads, window_start, now, now)
    except ReconciliationError as exc:
        print(f"\nFigures do not reconcile: {exc}", file=sys.stderr)
        print("Refusing to render. Fix the calculation.", file=sys.stderr)
        return 3
    say("Figures reconcile.")

    html = render(
        report=report,
        threads=threads,
        owner_name=config.owner_name,
        counts=counts,
        cascade=cascade,
        llm_used=llm_used,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(html)

    o = report.overall
    say(
        f"\nWrote {args.out}\n"
        f"  {o.needing} threads needed a reply · "
        f"avg {o.fmt(o.avg_hours)}h · median {o.fmt(o.median_hours)}h · "
        f"hit rate {o.hit_rate_pct} · {o.breached_open} open breach(es)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
