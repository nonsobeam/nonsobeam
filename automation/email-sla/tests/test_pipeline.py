"""Checks on threading, the anchor rule, classification and reconciliation."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from classify import classify  # noqa: E402
from schema import Message, Person, normalise_subject  # noqa: E402
from sla import ReconciliationError, build_report, reconcile  # noqa: E402
from threads import build_threads, due_today, group_threads  # noqa: E402
from workinghours import LISBON, UTC  # noqa: E402

ME = {"thaddeus.uzornne@wetransact.io"}
OWNER = Person("Thaddeus Uzornne", "thaddeus.uzornne@wetransact.io")
CLIENT = Person("Priya Raghunathan", "priya.raghunathan@northwind.example")
OTHER = Person("Aoife Brennan", "aoife.brennan@cobalt.example")
COLLEAGUE = Person("Bea Lindqvist", "bea.lindqvist@wetransact.io")

_n = [0]


def msg(subject, sender, to, when, folder="inbox", cc=None, body="", headers=None):
    _n[0] += 1
    return Message(
        uid=f"m{_n[0]}",
        folder=folder,
        subject=subject,
        sent_utc=when.astimezone(UTC),
        sender=sender,
        to=to,
        cc=cc or [],
        body_preview=body,
        headers=headers or {},
    )


def lx(y, m, d, hh=9, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=LISBON)


# --- subject normalisation ------------------------------------------------


def test_normalise_strips_stacked_prefixes():
    assert normalise_subject("Re: Fw: RE: Contract") == "contract"
    assert normalise_subject("Aceptado: Sync") == "sync"
    assert normalise_subject("TR: RV: Offre") == "offre"
    assert normalise_subject("Re[2]: Pricing") == "pricing"
    assert normalise_subject("[EXTERNAL] Re: Pricing") == "pricing"


# --- threading ------------------------------------------------------------


def test_same_subject_different_people_stay_apart():
    a = msg("Quick question", CLIENT, [OWNER], lx(2026, 8, 4, 10))
    b = msg("Quick question", OTHER, [OWNER], lx(2026, 8, 4, 11))
    groups = group_threads([a, b], ME)
    assert len(groups) == 2, "unrelated threads with the same subject were merged"


def test_reply_joins_its_thread():
    a = msg("Pricing", CLIENT, [OWNER], lx(2026, 8, 4, 10))
    b = msg("Re: Pricing", OWNER, [CLIENT], lx(2026, 8, 4, 12), folder="sent")
    groups = group_threads([a, b], ME)
    assert len(groups) == 1 and len(groups[0]) == 2


def test_blank_subjects_never_merge():
    a = msg("", CLIENT, [OWNER], lx(2026, 8, 4, 10))
    b = msg("", OTHER, [OWNER], lx(2026, 8, 4, 11))
    assert len(group_threads([a, b], ME)) == 2


# --- classification -------------------------------------------------------


def test_newsletter_excluded_by_header():
    m = msg("Marketplace Weekly", OTHER, [OWNER], lx(2026, 8, 4, 10),
            headers={"List-Unsubscribe": "<mailto:x@y.z>"})
    assert not classify(m, ME).needs_reply


def test_out_of_office_excluded():
    m = msg("Automatic reply: Out of office", CLIENT, [OWNER], lx(2026, 8, 4, 10),
            headers={"Auto-Submitted": "auto-replied"})
    assert not classify(m, ME).needs_reply


def test_calendar_response_excluded():
    m = msg("Accepted: QBR", CLIENT, [OWNER], lx(2026, 8, 4, 10),
            headers={"Content-Class": "urn:content-classes:calendarmessage"})
    assert not classify(m, ME).needs_reply


def test_signature_request_counts_despite_being_automated():
    m = msg("Signature requested: MSA", Person("DocuSign", "dse@docusign.net"),
            [OWNER], lx(2026, 8, 4, 10))
    d = classify(m, ME)
    assert d.needs_reply, "a signature request is a real obligation"


def test_org_invitation_counts():
    m = msg("Ruben has invited you to join Marisol Robotics",
            Person("Marisol", "invitations@marisol.example"), [OWNER],
            lx(2026, 8, 4, 10))
    assert classify(m, ME).needs_reply


def test_direct_question_counts():
    m = msg("Pricing", CLIENT, [OWNER], lx(2026, 8, 4, 10),
            body="Could you confirm the offer expiry?")
    assert classify(m, ME).needs_reply


def test_colleague_owning_next_step_excluded():
    m = msg("Integration testing", CLIENT, [COLLEAGUE], lx(2026, 8, 4, 10),
            cc=[OWNER], body="I'll take this one and come back to the group.")
    assert not classify(m, ME).needs_reply


def test_own_sequencing_mail_excluded():
    m = msg("Warm up", OWNER, [OWNER], lx(2026, 8, 4, 10))
    assert not classify(m, ME).needs_reply


# --- the anchor rule ------------------------------------------------------


def _decide(messages):
    return {m.uid: classify(m, ME) for m in messages if m.is_inbound}


def test_anchor_is_the_most_recent_inbound_needing_reply():
    first = msg("Offer", CLIENT, [OWNER], lx(2026, 8, 4, 10),
                body="Could you confirm the expiry?")
    reply = msg("Re: Offer", OWNER, [CLIENT], lx(2026, 8, 4, 12), folder="sent")
    second = msg("Re: Offer", CLIENT, [OWNER], lx(2026, 8, 5, 10),
                 body="Could you also send the checklist?")
    now = lx(2026, 8, 5, 13)
    threads = build_threads([first, reply, second], ME, _decide([first, second]), now)

    assert len(threads) == 1, "one row per thread"
    t = threads[0]
    assert t.anchor is second, "anchor must be the latest inbound needing a reply"
    assert t.reply is None, "the earlier reply predates the anchor"
    assert abs(t.hours - 3.0) < 1e-6
    assert t.status == "awaiting"


def test_reply_after_anchor_is_matched():
    inbound = msg("Offer", CLIENT, [OWNER], lx(2026, 8, 4, 10),
                  body="Could you confirm?")
    reply = msg("Re: Offer", OWNER, [CLIENT], lx(2026, 8, 4, 14), folder="sent")
    now = lx(2026, 8, 5, 9)
    t = build_threads([inbound, reply], ME, _decide([inbound]), now)[0]
    assert t.reply is reply
    assert abs(t.hours - 4.0) < 1e-6
    assert t.status == "replied" and t.in_target is True


def test_breach_when_over_target():
    inbound = msg("Escalation", CLIENT, [OWNER], lx(2026, 8, 4, 10),
                  body="Please approve this today.")
    reply = msg("Re: Escalation", OWNER, [CLIENT], lx(2026, 8, 6, 10), folder="sent")
    now = lx(2026, 8, 6, 12)
    t = build_threads([inbound, reply], ME, _decide([inbound]), now)[0]
    assert t.status == "breached" and not t.is_open
    assert t.in_target is False


def test_out_of_hours_anchor_starts_next_morning():
    inbound = msg("Offer", CLIENT, [OWNER], lx(2026, 8, 4, 22),
                  body="Could you confirm?")
    reply = msg("Re: Offer", OWNER, [CLIENT], lx(2026, 8, 5, 11), folder="sent")
    now = lx(2026, 8, 5, 12)
    t = build_threads([inbound, reply], ME, _decide([inbound]), now)[0]
    assert abs(t.hours - 2.0) < 1e-6, "clock starts at 09:00, not on arrival"


def test_due_today_lists_threads_breaching_before_close():
    # Arrives 09:00 Tuesday, so it breaches 09:00 Wednesday... already past.
    # Use one arriving 10:00 today that breaches at 10:00 tomorrow -> not due today.
    inbound = msg("Offer", CLIENT, [OWNER], lx(2026, 8, 5, 8, 30),
                  body="Could you confirm?")
    now = lx(2026, 8, 5, 14)
    threads = build_threads([inbound], ME, _decide([inbound]), now)
    # Landed 08:30 -> clock from 09:00 -> breaches 18:00 today.
    due = due_today(threads, now)
    assert len(due) == 1 and due[0].breach_at == lx(2026, 8, 5, 18)


# --- reconciliation -------------------------------------------------------


def test_report_reconciles_and_excludes_open_from_average():
    replied_in = msg("A", CLIENT, [OWNER], lx(2026, 8, 4, 10), body="Could you confirm?")
    r1 = msg("Re: A", OWNER, [CLIENT], lx(2026, 8, 4, 13), folder="sent")
    breached = msg("B", OTHER, [OWNER], lx(2026, 8, 4, 10), body="Could you confirm?")
    r2 = msg("Re: B", OWNER, [OTHER], lx(2026, 8, 6, 10), folder="sent")
    still_open = msg("C", Person("Caio", "caio@petravox.example"), [OWNER],
                     lx(2026, 8, 6, 15), body="Could you confirm?")
    now = lx(2026, 8, 6, 17)

    all_msgs = [replied_in, r1, breached, r2, still_open]
    threads = build_threads(all_msgs, ME, _decide(all_msgs), now)
    report = build_report(threads, lx(2026, 8, 1), now, now)
    o = report.overall

    assert o.needing == 3
    assert o.replied_in_target + o.breached + o.still_open == o.needing
    assert o.replied_count == 2, "the open thread must not be in the average"
    # A replied at 3h; B spans Tue 10:00 to Thu 10:00 = 8 + 9 + 1 = 18h.
    # Mean of the two replied threads is 10.5. Had the open thread (2h so far)
    # been averaged in as a zero, this would read 7.0 instead.
    assert abs(o.avg_hours - 10.5) < 1e-6, f"avg was {o.avg_hours}"
    assert abs(o.median_hours - 10.5) < 1e-6
    reconcile(o)


def test_reconcile_catches_a_broken_count():
    m = msg("A", CLIENT, [OWNER], lx(2026, 8, 4, 10), body="Could you confirm?")
    now = lx(2026, 8, 4, 12)
    threads = build_threads([m], ME, _decide([m]), now)
    report = build_report(threads, lx(2026, 8, 1), now, now)
    o = report.overall
    o.needing += 1  # corrupt it
    try:
        reconcile(o)
    except ReconciliationError:
        return
    raise AssertionError("reconcile() failed to catch a broken count")


def test_weekly_counts_sum_to_overall():
    msgs = []
    for day in range(1, 40):
        when = lx(2026, 7, 1) + timedelta(days=day)
        if when.weekday() >= 5:
            continue
        m = msg(f"Thread {day}", CLIENT, [OWNER], when.replace(hour=10),
                body="Could you confirm?")
        msgs.append(m)
        if day % 3:
            msgs.append(
                msg(f"Re: Thread {day}", OWNER, [CLIENT],
                    when.replace(hour=14), folder="sent")
            )
    now = lx(2026, 8, 20, 12)
    threads = build_threads(msgs, ME, _decide(msgs), now)
    report = build_report(threads, lx(2026, 7, 1), now, now)
    assert sum(m.needing for m in report.weekly) == report.overall.needing
    assert abs(sum(m.total_hours for m in report.weekly)
               - report.overall.total_hours) < 1e-6


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"pass  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
