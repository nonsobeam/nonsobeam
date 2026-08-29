"""Render the self-contained HTML dashboard.

Everything is inlined: no CDN, no external stylesheet, no remote font, no
http(s) reference anywhere in the output. Charts are SVG generated here rather
than by a charting library, which is what keeps that promise cheaply.

Colour follows the reserved-status rule: one blue for the data series, and
green/red for replied/breached -- but those two are near-identical under
deuteranopia, so every status also carries a glyph and a word. Colour never
carries meaning on its own.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta

from sla import Metrics, Report
from threads import Thread, due_today, hours_remaining
from workinghours import TARGET_HOURS, to_lisbon

# --- palette -------------------------------------------------------------
# Validated steps; light and dark are each selected for their own surface.
LIGHT = {
    "plane": "#f9f9f7", "surface": "#fcfcfb",
    "primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "axis": "#c3c2b7", "border": "rgba(11,11,11,0.10)",
    "series": "#2a78d6", "good": "#0ca30c", "critical": "#d03b3b",
    "warning": "#fab219",
}
DARK = {
    "plane": "#0d0d0d", "surface": "#1a1a19",
    "primary": "#ffffff", "secondary": "#c3c2b7", "muted": "#898781",
    "grid": "#2c2c2a", "axis": "#383835", "border": "rgba(255,255,255,0.10)",
    "series": "#3987e5", "good": "#0ca30c", "critical": "#d03b3b",
    "warning": "#fab219",
}

STATUS = {
    "replied": ("Replied", "✓", "good"),
    "breached": ("Breached", "▲", "critical"),
    "awaiting": ("Awaiting reply", "•", "warning"),
}


def e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def status_badge(status: str) -> str:
    label, glyph, role = STATUS[status]
    return (
        f'<span class="badge badge-{role}">'
        f'<span class="glyph" aria-hidden="true">{glyph}</span>{e(label)}</span>'
    )


def fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%a %-d %b %Y, %H:%M") if dt else "—"


def fmt_hours(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


# --- charts --------------------------------------------------------------

SLOT = 54          # px per week; wide enough that count labels never collide
PAD_L, PAD_R = 56, 24
PAD_T, PAD_B = 20, 52
PLOT_H = 200


def _chart_width(n: int) -> int:
    return PAD_L + PAD_R + max(1, n) * SLOT


def line_chart(weekly: list[Metrics]) -> str:
    """Average working hours to reply, by week, against the target line."""
    points = [(i, m) for i, m in enumerate(weekly) if m.avg_hours is not None]
    if not points:
        return '<p class="empty">No replied threads yet, so there is no average to plot.</p>'

    width = _chart_width(len(weekly))
    top = max([m.avg_hours for _, m in points] + [TARGET_HOURS]) * 1.15
    top = max(top, 1.0)

    def x(i: int) -> float:
        return PAD_L + i * SLOT + SLOT / 2

    def y(v: float) -> float:
        return PAD_T + PLOT_H - (v / top) * PLOT_H

    parts = [
        f'<svg viewBox="0 0 {width} {PAD_T + PLOT_H + PAD_B}" width="{width}" '
        f'height="{PAD_T + PLOT_H + PAD_B}" role="img" '
        f'aria-label="Average working hours to first reply by week, against a '
        f'{TARGET_HOURS:g} working hour target">'
    ]

    # Horizontal gridlines and y ticks.
    steps = 4
    for s in range(steps + 1):
        v = top * s / steps
        yy = y(v)
        parts.append(
            f'<line class="grid" x1="{PAD_L}" y1="{yy:.1f}" x2="{width - PAD_R}" '
            f'y2="{yy:.1f}"/>'
            f'<text class="tick" x="{PAD_L - 10}" y="{yy + 4:.1f}" '
            f'text-anchor="end">{v:.0f}</text>'
        )

    # The target: dashed, labelled, so it reads as a threshold not a series.
    ty = y(TARGET_HOURS)
    parts.append(
        f'<line class="target" x1="{PAD_L}" y1="{ty:.1f}" x2="{width - PAD_R}" '
        f'y2="{ty:.1f}"/>'
        f'<text class="target-label" x="{width - PAD_R}" y="{ty - 7:.1f}" '
        f'text-anchor="end">{TARGET_HOURS:g}h target</text>'
    )

    path = " ".join(
        f"{'M' if k == 0 else 'L'}{x(i):.1f},{y(m.avg_hours):.1f}"
        for k, (i, m) in enumerate(points)
    )
    parts.append(f'<path class="series-line" d="{path}"/>')

    # Direct labels only where they earn their place: first, last, and any week
    # over target. A number on every point is noise.
    over = {i for i, m in points if m.avg_hours > TARGET_HOURS}
    labelled = {points[0][0], points[-1][0]} | over

    for i, m in points:
        cx, cy = x(i), y(m.avg_hours)
        cls = "marker-partial" if m.partial else "marker"
        parts.append(
            f'<circle class="{cls}" cx="{cx:.1f}" cy="{cy:.1f}" r="4.5">'
            f"<title>{e(m.label)}: {m.avg_hours:.2f} working hours, "
            f"{m.replied_count} replied{' (partial week)' if m.partial else ''}</title>"
            f"</circle>"
        )
        if i in labelled:
            parts.append(
                f'<text class="point-label" x="{cx:.1f}" y="{cy - 12:.1f}" '
                f'text-anchor="middle">{m.avg_hours:.2f}</text>'
            )

    for i, m in enumerate(weekly):
        parts.append(
            f'<text class="axis-label" x="{x(i):.1f}" y="{PAD_T + PLOT_H + 18}" '
            f'text-anchor="middle">{e(m.label)}</text>'
        )
        if m.partial:
            parts.append(
                f'<text class="axis-note" x="{x(i):.1f}" y="{PAD_T + PLOT_H + 32}" '
                f'text-anchor="middle">partial</text>'
            )

    parts.append("</svg>")
    return f'<div class="scroll">{"".join(parts)}</div>'


def bar_chart(weekly: list[Metrics]) -> str:
    """Hit rate by week, each bar labelled with the counts behind it."""
    if not weekly:
        return '<p class="empty">No weeks in range.</p>'

    width = _chart_width(len(weekly))
    bar_w = 26

    def x(i: int) -> float:
        return PAD_L + i * SLOT + (SLOT - bar_w) / 2

    def y(v: float) -> float:
        return PAD_T + PLOT_H - v * PLOT_H

    parts = [
        f'<svg viewBox="0 0 {width} {PAD_T + PLOT_H + PAD_B}" width="{width}" '
        f'height="{PAD_T + PLOT_H + PAD_B}" role="img" '
        f'aria-label="Share of threads replied inside target, by week">'
    ]
    for s in range(5):
        v = s / 4
        yy = y(v)
        parts.append(
            f'<line class="grid" x1="{PAD_L}" y1="{yy:.1f}" x2="{width - PAD_R}" '
            f'y2="{yy:.1f}"/>'
            f'<text class="tick" x="{PAD_L - 10}" y="{yy + 4:.1f}" '
            f'text-anchor="end">{v * 100:.0f}%</text>'
        )

    for i, m in enumerate(weekly):
        if m.hit_rate is None:
            continue
        h = max(2.0, m.hit_rate * PLOT_H)
        top_y = PAD_T + PLOT_H - h
        parts.append(
            f'<rect class="bar{" bar-partial" if m.partial else ""}" '
            f'x="{x(i):.1f}" y="{top_y:.1f}" width="{bar_w}" height="{h:.1f}" '
            f'rx="4">'
            f"<title>{e(m.label)}: {m.replied_in_target} of {m.needing} inside "
            f"target ({m.hit_rate_pct})"
            f"{' — partial week' if m.partial else ''}</title></rect>"
            f'<text class="bar-value" x="{x(i) + bar_w / 2:.1f}" '
            f'y="{top_y - 16:.1f}" text-anchor="middle">{m.hit_rate_pct}</text>'
            f'<text class="bar-count" x="{x(i) + bar_w / 2:.1f}" '
            f'y="{top_y - 5:.1f}" text-anchor="middle">'
            f"{m.replied_in_target}/{m.needing}</text>"
        )

    parts.append(
        f'<line class="axis-line" x1="{PAD_L}" y1="{PAD_T + PLOT_H}" '
        f'x2="{width - PAD_R}" y2="{PAD_T + PLOT_H}"/>'
    )
    for i, m in enumerate(weekly):
        parts.append(
            f'<text class="axis-label" x="{x(i) + bar_w / 2:.1f}" '
            f'y="{PAD_T + PLOT_H + 18}" text-anchor="middle">{e(m.label)}</text>'
        )
        if m.partial:
            parts.append(
                f'<text class="axis-note" x="{x(i) + bar_w / 2:.1f}" '
                f'y="{PAD_T + PLOT_H + 32}" text-anchor="middle">partial</text>'
            )
    parts.append("</svg>")
    return f'<div class="scroll">{"".join(parts)}</div>'


# --- tables --------------------------------------------------------------


def breaches_table(threads: list[Thread]) -> str:
    breached = sorted(
        [t for t in threads if t.status == "breached"],
        key=lambda t: t.hours,
        reverse=True,
    )
    if not breached:
        return '<p class="empty">Nothing has passed nine working hours. ✓</p>'

    rows = []
    for t in breached:
        state = (
            '<span class="badge badge-critical"><span class="glyph" '
            'aria-hidden="true">▲</span>Breached, still open</span>'
            if t.is_open
            else '<span class="badge badge-serious"><span class="glyph" '
            'aria-hidden="true">▲</span>Breached, since answered</span>'
        )
        rows.append(
            f"<tr><td class='num'>{t.hours:.2f}</td>"
            f"<td>{e(t.subject)}</td><td>{e(t.sender)}</td>"
            f"<td>{e(t.account_label)}</td>"
            f"<td>{e(fmt_dt(t.received_lisbon))}</td><td>{state}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Working hrs</th><th>Thread</th><th>From</th>"
        "<th>Account</th><th>Received (Lisbon)</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def weekly_table(report: Report) -> str:
    rows = []
    for m in report.weekly:
        label = e(m.label) + (' <span class="pill">partial</span>' if m.partial else "")
        rows.append(
            f"<tr><td>{label}</td><td class='num'>{m.needing}</td>"
            f"<td class='num'>{m.replied_in_target}</td>"
            f"<td class='num'>{m.hit_rate_pct}</td>"
            f"<td class='num'>{fmt_hours(m.avg_hours)}</td>"
            f"<td class='num'>{fmt_hours(m.median_hours)}</td>"
            f"<td class='num'>{m.breached}</td><td class='num'>{m.still_open}</td></tr>"
        )
    o = report.overall
    rows.append(
        f"<tr class='total'><td>All weeks</td><td class='num'>{o.needing}</td>"
        f"<td class='num'>{o.replied_in_target}</td>"
        f"<td class='num'>{o.hit_rate_pct}</td>"
        f"<td class='num'>{fmt_hours(o.avg_hours)}</td>"
        f"<td class='num'>{fmt_hours(o.median_hours)}</td>"
        f"<td class='num'>{o.breached}</td><td class='num'>{o.still_open}</td></tr>"
    )
    return (
        "<table><thead><tr><th>Week starting</th><th>Needing reply</th>"
        "<th>Replied in target</th><th>Hit rate</th><th>Avg hrs</th>"
        "<th>Median hrs</th><th>Breached</th><th>Still open</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def all_threads_table(threads: list[Thread]) -> str:
    rows = []
    for t in threads:
        in_target = {True: "Yes", False: "No", None: "—"}[t.in_target]
        rows.append(
            f"<tr><td>{e(t.subject)}</td><td>{e(t.sender)}</td>"
            f"<td>{e(t.account_label)}</td>"
            f"<td>{e(fmt_dt(t.received_lisbon))}</td>"
            f"<td>{e(t.week.strftime('%-d %b %Y'))}</td>"
            f"<td class='num'>{t.hours:.2f}</td>"
            f"<td>{status_badge(t.status)}</td>"
            f"<td>{e(fmt_dt(t.replied_lisbon))}</td>"
            f"<td>{in_target}</td><td class='note'>{e(t.rule)}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Thread</th><th>From</th><th>Account</th>"
        "<th>Received (Lisbon)</th><th>Week starting</th><th>Working hrs open</th>"
        "<th>Status</th><th>Replied on (Lisbon)</th><th>In target</th>"
        "<th>Note</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def due_today_block(threads: list[Thread], now: datetime) -> str:
    due = due_today(threads, now)
    if not due:
        return '<p class="empty">Nothing breaches before 18:00 today. ✓</p>'
    items = "".join(
        f"<li><b>{e(t.subject)}</b> — {e(t.sender)}"
        f"{'' if not t.account else ' (' + e(t.account) + ')'}: "
        f"<b>{hours_remaining(t):.2f}</b> working hours left, breaches at "
        f"<b>{e(t.breach_at.strftime('%H:%M'))}</b>.</li>"
        for t in due
    )
    return f"<ul class='due'>{items}</ul>"


# --- page ----------------------------------------------------------------


def _css() -> str:
    def block(p: dict) -> str:
        return "".join(f"--{k}:{v};" for k, v in p.items())

    return f"""
:root{{color-scheme:light;{block(LIGHT)}}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;{block(DARK)}}}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:32px 20px 64px;background:var(--plane);color:var(--primary);
font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 6px;letter-spacing:-0.01em}}
h2{{font-size:16px;margin:34px 0 10px;letter-spacing:-0.005em}}
.sub{{color:var(--secondary);margin:0 0 4px}}
.meta{{color:var(--muted);font-size:12.5px;margin:0 0 24px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:14px}}
.tile{{background:var(--surface);border:1px solid var(--border);border-radius:10px;
padding:16px 18px}}
.tile .label{{color:var(--secondary);font-size:12.5px;margin-bottom:6px}}
.tile .value{{font-size:30px;font-weight:600;letter-spacing:-0.02em;line-height:1.1}}
.tile .value .unit{{font-size:15px;font-weight:500;color:var(--secondary);
margin-left:3px}}
.tile .foot{{color:var(--muted);font-size:11.5px;margin-top:7px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;
padding:16px 18px;margin-top:10px}}
.scroll{{overflow-x:auto;overflow-y:hidden}}
.grid{{stroke:var(--grid);stroke-width:1}}
.axis-line{{stroke:var(--axis);stroke-width:1}}
.tick,.axis-label{{fill:var(--muted);font-size:11px;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
font-variant-numeric:tabular-nums}}
.axis-note{{fill:var(--muted);font-size:9.5px;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif}}
.series-line{{fill:none;stroke:var(--series);stroke-width:2;
stroke-linejoin:round;stroke-linecap:round}}
.marker{{fill:var(--series);stroke:var(--surface);stroke-width:2}}
.marker-partial{{fill:var(--surface);stroke:var(--series);stroke-width:2}}
.point-label{{fill:var(--secondary);font-size:10.5px;font-weight:600;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
font-variant-numeric:tabular-nums}}
.target{{stroke:var(--secondary);stroke-width:1.5;stroke-dasharray:6 4}}
.target-label{{fill:var(--secondary);font-size:10.5px;font-weight:600;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif}}
.bar{{fill:var(--series)}}
.bar-partial{{fill:var(--series);opacity:.55}}
.bar-value{{fill:var(--primary);font-size:11px;font-weight:600;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
font-variant-numeric:tabular-nums}}
.bar-count{{fill:var(--muted);font-size:9.5px;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
font-variant-numeric:tabular-nums}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--grid);
vertical-align:top}}
th{{color:var(--secondary);font-weight:600;font-size:12px;white-space:nowrap}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
td.note{{color:var(--muted);font-size:12px}}
tr.total td{{font-weight:700;border-top:2px solid var(--axis)}}
.badge{{display:inline-flex;align-items:center;gap:5px;white-space:nowrap;
font-size:12px;font-weight:600}}
.glyph{{font-size:11px}}
.badge-good{{color:var(--good)}}
.badge-critical{{color:var(--critical)}}
.badge-serious{{color:var(--secondary)}}
.badge-warning{{color:var(--secondary)}}
.pill{{display:inline-block;background:var(--grid);color:var(--secondary);
border-radius:20px;padding:1px 7px;font-size:10.5px;font-weight:600}}
.empty{{color:var(--secondary);margin:6px 0}}
.hint{{color:var(--muted);font-size:12px;margin:0 0 2px}}
.trend{{background:var(--surface);border:1px solid var(--border);border-left:3px
solid var(--series);border-radius:8px;padding:12px 15px;margin:14px 0}}
.due li{{margin-bottom:7px}}
details{{margin-top:10px;background:var(--surface);border:1px solid var(--border);
border-radius:10px;padding:2px 18px 14px}}
summary{{cursor:pointer;padding:13px 0;font-weight:600;font-size:14px}}
summary::marker{{color:var(--muted)}}
footer{{margin-top:40px;padding-top:18px;border-top:1px solid var(--grid);
color:var(--muted);font-size:12px}}
footer p{{margin:0 0 6px}}
"""


def render(
    report: Report,
    threads: list[Thread],
    owner_name: str,
    counts: dict[str, int],
    cascade: dict[str, int],
    llm_used: bool,
) -> str:
    o = report.overall
    # Everything shown to the reader is Lisbon wall time; the report carries UTC.
    now = to_lisbon(report.now)
    open_breaches = o.breached_open
    # Over a long backfill most open breaches are simply dead threads from months
    # back. Splitting recent from old is what makes the headline count actionable.
    cutoff = report.now - timedelta(days=30)
    recent_open = sum(
        1 for t in threads
        if t.status == "breached" and t.is_open and t.anchor.sent_utc >= cutoff
    )

    tiles = [
        (
            "Average working hours to first reply",
            fmt_hours(o.avg_hours), "h",
            f"{o.replied_count} replied threads, {o.still_open + o.breached_open} "
            f"open thread(s) excluded",
        ),
        (
            "Median working hours to first reply",
            fmt_hours(o.median_hours), "h",
            f"same {o.replied_count} replied threads; the mean is dragged by "
            f"long tails, the median is not",
        ),
        (
            "Hit rate inside target",
            o.hit_rate_pct, "",
            f"{o.replied_in_target} replied inside {TARGET_HOURS:g}h of "
            f"{o.needing} threads needing a reply",
        ),
        (
            "Open breaches",
            str(open_breaches), "",
            f"past {TARGET_HOURS:g} working hours and still unanswered — "
            f"{recent_open} from the last 30 days, {open_breaches - recent_open} "
            f"older; {o.breached_answered} more breached but were answered",
        ),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="label">{e(label)}</div>'
        f'<div class="value">{e(value)}'
        f'{f"<span class=unit>{e(unit)}</span>" if unit else ""}</div>'
        f'<div class="foot">{e(foot)}</div></div>'
        for label, value, unit, foot in tiles
    )

    window = (
        f"{to_lisbon(report.window_start).strftime('%-d %b %Y')} to "
        f"{to_lisbon(report.window_end).strftime('%-d %b %Y')}"
    )
    outlier = report.outlier_note()
    trend_html = f"<p>{e(report.trend())}</p>" + (
        f"<p>{e(outlier)}</p>" if outlier else ""
    )

    source_note = (
        "Classified offline by rule, with no model call."
        if not llm_used
        else f"Rules settled {cascade['needs_reply'] + cascade['no_reply']} messages "
        f"offline; {cascade['uncertain']} ambiguous ones went to the model."
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Email SLA — {e(owner_name)}</title>
<style>{_css()}</style></head>
<body><div class="wrap">

<h1>Email SLA — {e(owner_name)}</h1>
<p class="sub">Scanned {e(fmt_dt(now))} Europe/Lisbon · window {e(window)} ·
target {TARGET_HOURS:g} working hours (Mon–Fri, 09:00–18:00 Lisbon)</p>
<p class="meta">{counts['inbox']} inbox and {counts['sent']} sent messages read ·
{o.needing} threads needed a reply · {e(source_note)}</p>

<div class="tiles">{tile_html}</div>

<h2>Average working hours to first reply, by week</h2>
<p class="hint">{len(report.weekly)} weeks — scroll the chart sideways for the
full range, or open the weekly table below.</p>
<div class="card">{line_chart(report.weekly)}</div>

<h2>Hit rate by week</h2>
<p class="hint">Each bar is labelled with the counts behind it: replied inside
target, of threads needing a reply.</p>
<div class="card">{bar_chart(report.weekly)}</div>

<div class="trend">{trend_html}</div>

<h2>Due today</h2>
{due_today_block(threads, now)}

<h2>Breaches, worst first</h2>
{breaches_table(threads)}

<details><summary>Weekly figures ({len(report.weekly)} weeks)</summary>
{weekly_table(report)}</details>

<details><summary>Every thread in the window ({len(threads)}), oldest first</summary>
{all_threads_table(threads)}</details>

<footer>
<p>Source: Outlook inbox and sent items, read from a local export, read-only.
{counts['inbox']} inbox and {counts['sent']} sent messages parsed.</p>
<p>Window: {e(window)}. Anchor rule: each thread is measured from the most recent
inbound message that needed a reply (or 09:00 the next working day, if it landed
out of hours) to the first reply after it, or to now if there is none.</p>
<p>“No reply needed” is yours to set by hand; this scan never assigns it.
Over a window this long, an open breach from months back is usually a thread that
died rather than one still waiting on you — the open-breach tile splits the last
30 days from the rest for that reason.</p>
<p>{e(source_note)} Status is shown with a word and a glyph as well as colour,
because red and green are near-identical under deuteranopia.</p>
</footer>
</div></body></html>"""
