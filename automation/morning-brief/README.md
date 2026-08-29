# Morning brief automation

SLA + planning brief for Thaddeus, run by a scheduled Claude Code trigger
(Monday and Thursday, 08:00 Europe/Lisbon — changed 2026-08-29 from daily
weekdays to twice a week to cut token/credit spend; see below). See `TASK.md`
for the full task spec the scheduled run follows — that file is the actual
prompt, not just documentation; edit it there to change how the automation
behaves.

## Where things are

- `TASK.md` — the task spec / prompt the trigger executes.
- `state/` — durable state between runs (`history.jsonl`, `threads-latest.json`,
  `carry-forward.json`, `commitments.json`, `people.json`,
  `no-reply-needed.json` — this last one is Thaddeus's to edit by hand, the
  automation only reads it).
- `output/brief-YYYY-MM-DD.html` — one file per day, the actual brief. This,
  plus the copy pushed into that day's session as a file, is where the report
  lives day to day. **No email is ever sent** — see below.

## Why this exists

The original version of this task re-read the full 14-day Outlook window from
scratch every single day, which was by far the most expensive part of the run
(hundreds of thousands of tokens on the Inbox scan alone some days). This version
persists thread state here in git so each run only needs to read what's new since
the last run, then recompute SLA/breach status for already-known open threads
with plain arithmetic instead of re-fetching them.

## Schedule: twice a week, not daily

Changed 2026-08-29: the trigger (`Twice-weekly morning brief (Thaddeus)`) now
fires Monday and Thursday at 08:00 Europe/Lisbon instead of every weekday, to
cut the number of runs — and their token/credit cost — further, on top of the
incremental-read savings above. No code change was needed for this: the
incremental read pass (`TASK.md` Section 3) already keys off the prior run's
`last_run_at` rather than assuming a fixed daily gap, so it reads whatever
built up since the last run (a weekend plus Monday, or Tuesday through
Thursday) exactly the same way it read a single day's gap before.

## Delivery: report only, no email

Thaddeus explicitly asked for report-only delivery — `outlook_send_mail` is never
called. The report is written to `output/brief-YYYY-MM-DD.html`, committed here,
and pushed to him as a file in the session that ran it. (For the record, it was
also blocked by a `403 FORBIDDEN` — the connector's `Mail.Send` permission was
never consented — but that's now moot: even if that permission were granted,
email sending stays off unless Thaddeus asks for it again.)
