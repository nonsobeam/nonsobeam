"""Checks on the working-hours clock, including the worked examples in the spec."""

from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workinghours import (  # noqa: E402
    LISBON,
    UTC,
    add_working_hours,
    next_working_start,
    week_start,
    working_hours_between,
)


def lx(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=LISBON)


def approx(a, b, tol=1e-6):
    assert abs(a - b) < tol, f"{a} != {b}"


def test_tuesday_1500_is_due_wednesday_1500():
    # 2026-08-04 is a Tuesday.
    approx(working_hours_between(lx(2026, 8, 4, 15), lx(2026, 8, 5, 15)), 9.0)
    assert add_working_hours(lx(2026, 8, 4, 15), 9.0) == lx(2026, 8, 5, 15)


def test_friday_1600_is_due_monday_1600():
    # 2026-08-07 is a Friday; the weekend must not count.
    approx(working_hours_between(lx(2026, 8, 7, 16), lx(2026, 8, 10, 16)), 9.0)
    assert add_working_hours(lx(2026, 8, 7, 16), 9.0) == lx(2026, 8, 10, 16)


def test_out_of_hours_arrival_starts_next_morning():
    # Tuesday 22:30 -> clock starts Wednesday 09:00.
    assert next_working_start(lx(2026, 8, 4, 22, 30)) == lx(2026, 8, 5, 9)
    # So a Wednesday 11:00 reply is two hours, not twelve and a half.
    approx(working_hours_between(lx(2026, 8, 4, 22, 30), lx(2026, 8, 5, 11)), 2.0)


def test_weekend_arrival_starts_monday():
    # 2026-08-08 is a Saturday.
    assert next_working_start(lx(2026, 8, 8, 10)) == lx(2026, 8, 10, 9)
    approx(working_hours_between(lx(2026, 8, 8, 10), lx(2026, 8, 10, 12)), 3.0)


def test_within_one_day():
    approx(working_hours_between(lx(2026, 8, 4, 9, 30), lx(2026, 8, 4, 12, 0)), 2.5)


def test_spans_lunchless_evening():
    # 16:00 Tuesday to 10:00 Wednesday: 2h Tuesday + 1h Wednesday.
    approx(working_hours_between(lx(2026, 8, 4, 16), lx(2026, 8, 5, 10)), 3.0)


def test_reply_after_close_clamps_to_close():
    # Replying at 23:00 the same day counts only to 18:00.
    approx(working_hours_between(lx(2026, 8, 4, 15), lx(2026, 8, 4, 23)), 3.0)


def test_end_before_start_is_zero():
    approx(working_hours_between(lx(2026, 8, 5, 10), lx(2026, 8, 4, 10)), 0.0)


def test_utc_is_converted_not_assumed():
    # 2026-08-04 08:00 UTC is 09:00 Lisbon (WEST, UTC+1), i.e. the day's open.
    utc_open = datetime(2026, 8, 4, 8, 0, tzinfo=UTC)
    assert next_working_start(utc_open) == lx(2026, 8, 4, 9)
    # And in January (WET, UTC+0) the same wall time is an hour before open.
    jan = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)
    assert next_working_start(jan) == datetime(2026, 1, 6, 9, 0, tzinfo=LISBON)


def test_dst_changeover_does_not_distort_a_day():
    # Last Sunday in October 2026 is the 25th; the Monday after is a normal day.
    approx(working_hours_between(lx(2026, 10, 26, 9), lx(2026, 10, 26, 18)), 9.0)
    # And the Friday before the spring change (29 March 2026) likewise.
    approx(working_hours_between(lx(2026, 3, 27, 9), lx(2026, 3, 27, 18)), 9.0)


def test_multi_week_span():
    # Friday 17:00 to the Tuesday of the following week, 10:00.
    # Fri 1h + Mon 9h + Tue 1h = 11h.
    approx(working_hours_between(lx(2026, 8, 7, 17), lx(2026, 8, 11, 10)), 11.0)


def test_add_working_hours_rolls_over_weekend():
    # Friday 17:00 + 9h -> Monday 17:00.
    assert add_working_hours(lx(2026, 8, 7, 17), 9.0) == lx(2026, 8, 10, 17)


def test_add_and_measure_are_inverse():
    for hours in (0.5, 3.0, 9.0, 14.25, 40.0):
        start = lx(2026, 8, 4, 11, 15)
        approx(working_hours_between(start, add_working_hours(start, hours)), hours)


def test_week_start_is_monday_midnight():
    assert week_start(lx(2026, 8, 7, 16)) == lx(2026, 8, 3, 0)
    assert week_start(lx(2026, 8, 3, 9)) == lx(2026, 8, 3, 0)
    # Sunday belongs to the week that began the previous Monday.
    assert week_start(lx(2026, 8, 9, 12)) == lx(2026, 8, 3, 0)


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
