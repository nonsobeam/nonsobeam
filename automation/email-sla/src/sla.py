"""Metrics, weekly breakdown, and the reconciliation checks that gate rendering.

The spec is blunt about two traps and both are guarded here:

  * Open threads have no reply time and must never be averaged in as zero. The
    replied population and the needing-reply population are different sizes, and
    conflating them silently flatters the average.
  * The figures must add up. `reconcile()` raises before anything renders, so a
    broken calculation fails loudly instead of shipping a confident dashboard
    full of wrong numbers.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

_WEEK = timedelta(days=7)

from threads import Thread
from workinghours import TARGET_HOURS


class ReconciliationError(AssertionError):
    """Raised when the figures do not add up. Never caught -- fix the maths."""


@dataclass
class Metrics:
    label: str
    week: datetime | None
    threads: list[Thread] = field(default_factory=list)

    # Counts
    needing: int = 0
    replied_in_target: int = 0
    breached: int = 0
    still_open: int = 0
    breached_open: int = 0
    breached_answered: int = 0

    # Reply-time figures, over replied threads only
    replied_count: int = 0
    total_hours: float = 0.0
    avg_hours: float | None = None
    median_hours: float | None = None
    hit_rate: float | None = None

    partial: bool = False

    @property
    def hit_rate_pct(self) -> str:
        return "n/a" if self.hit_rate is None else f"{self.hit_rate * 100:.0f}%"

    def fmt(self, value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2f}"


def _compute(label: str, week: datetime | None, threads: list[Thread]) -> Metrics:
    m = Metrics(label=label, week=week, threads=threads)
    m.needing = len(threads)
    m.replied_in_target = sum(1 for t in threads if t.status == "replied")
    m.breached = sum(1 for t in threads if t.status == "breached")
    m.still_open = sum(1 for t in threads if t.status == "awaiting")
    m.breached_open = sum(1 for t in threads if t.status == "breached" and t.is_open)
    m.breached_answered = sum(
        1 for t in threads if t.status == "breached" and not t.is_open
    )

    # Reply-time population: threads that actually got a reply, breached or not.
    replied = [t.hours for t in threads if t.reply is not None]
    m.replied_count = len(replied)
    m.total_hours = sum(replied)
    if replied:
        m.avg_hours = m.total_hours / len(replied)
        m.median_hours = statistics.median(replied)
    if m.needing:
        m.hit_rate = m.replied_in_target / m.needing
    return m


def reconcile(m: Metrics) -> None:
    """Assert the figures add up. Raises ReconciliationError if they do not."""
    total = m.replied_in_target + m.breached + m.still_open
    if total != m.needing:
        raise ReconciliationError(
            f"{m.label}: statuses sum to {total} but {m.needing} threads need a reply "
            f"(replied={m.replied_in_target} breached={m.breached} open={m.still_open})"
        )
    if m.breached_open + m.breached_answered != m.breached:
        raise ReconciliationError(
            f"{m.label}: breached split {m.breached_open}+{m.breached_answered} "
            f"does not equal {m.breached}"
        )
    if m.replied_count:
        expected = m.total_hours / m.replied_count
        if m.avg_hours is None or abs(expected - m.avg_hours) > 1e-9:
            raise ReconciliationError(
                f"{m.label}: average {m.avg_hours} != {m.total_hours}/{m.replied_count}"
            )
    elif m.avg_hours is not None:
        raise ReconciliationError(f"{m.label}: average set with no replied threads")

    # An open thread has no reply time; it must not have leaked into the average.
    open_threads = [t for t in m.threads if t.reply is None]
    if m.replied_count + len(open_threads) != m.needing:
        raise ReconciliationError(
            f"{m.label}: replied {m.replied_count} + open {len(open_threads)} "
            f"!= needing {m.needing}"
        )
    if m.hit_rate is not None and not (0.0 <= m.hit_rate <= 1.0):
        raise ReconciliationError(f"{m.label}: hit rate {m.hit_rate} out of range")


@dataclass
class Report:
    overall: Metrics
    weekly: list[Metrics]
    window_start: datetime
    window_end: datetime
    now: datetime

    @property
    def this_week(self) -> Metrics | None:
        return self.weekly[-1] if self.weekly else None

    @property
    def prev_week(self) -> Metrics | None:
        return self.weekly[-2] if len(self.weekly) > 1 else None

    def trend(self) -> str:
        """One sentence on whether the average moved versus the previous week."""
        cur, prev = self.this_week, self.prev_week
        if not cur or not prev or cur.avg_hours is None or prev.avg_hours is None:
            return "No comparable previous week, so no trend to report."
        delta = cur.avg_hours - prev.avg_hours
        if abs(delta) < 0.01:
            return (
                f"The average held flat at {cur.avg_hours:.2f} working hours "
                f"versus the previous week."
            )
        direction = "up" if delta > 0 else "down"
        return (
            f"The average moved {direction} {abs(delta):.2f} working hours versus the "
            f"previous week, from {prev.avg_hours:.2f} to {cur.avg_hours:.2f}."
        )

    def outlier_note(self) -> str:
        """If one thread is carrying the current week, say so."""
        cur = self.this_week
        if not cur or cur.replied_count < 3 or cur.avg_hours is None:
            return ""
        replied = sorted(
            (t for t in cur.threads if t.reply is not None),
            key=lambda t: t.hours,
            reverse=True,
        )
        worst = replied[0]
        rest = [t.hours for t in replied[1:]]
        without = sum(rest) / len(rest)
        if worst.hours > 2 * without and worst.hours - without > 2.0:
            return (
                f"One thread is carrying the week: without “{worst.subject}” "
                f"at {worst.hours:.2f} working hours, the average would be "
                f"{without:.2f}."
            )
        return ""


def build_report(
    threads: list[Thread],
    window_start: datetime,
    window_end: datetime,
    now: datetime,
) -> Report:
    """Compute overall and weekly metrics, reconciling every one before returning."""
    overall = _compute("All weeks", None, threads)
    reconcile(overall)

    by_week: dict[datetime, list[Thread]] = {}
    for t in threads:
        by_week.setdefault(t.week, []).append(t)

    weekly: list[Metrics] = []
    for week in sorted(by_week):
        m = _compute(week.strftime("%-d %b"), week, by_week[week])
        # A week is partial if the window opened after it began, or if now falls
        # inside it -- either way its figures are not a full five days.
        m.partial = week < window_start or now < week + _WEEK
        reconcile(m)
        weekly.append(m)

    # Per-week counts must sum to the overall counts.
    for field_name in ("needing", "replied_in_target", "breached", "still_open",
                       "replied_count"):
        total = sum(getattr(m, field_name) for m in weekly)
        if total != getattr(overall, field_name):
            raise ReconciliationError(
                f"weekly {field_name} sums to {total}, overall says "
                f"{getattr(overall, field_name)}"
            )
    total_hours = sum(m.total_hours for m in weekly)
    if abs(total_hours - overall.total_hours) > 1e-6:
        raise ReconciliationError(
            f"weekly reply hours sum to {total_hours}, overall says {overall.total_hours}"
        )

    return Report(overall=overall, weekly=weekly, window_start=window_start,
                  window_end=window_end, now=now)
