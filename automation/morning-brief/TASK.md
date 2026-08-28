# Daily Morning Brief — SLA + Planning (v3, incremental)

This is the canonical task spec for Thaddeus's daily morning brief. It replaces the
one-off v2 prompt: the deliverable, ranking, and email format are unchanged — only
Section 3 (the read pass) changed, to stop re-reading the full 14-day mailbox from
scratch every single day.

State for this task lives in this repo, at `automation/morning-brief/state/` and
`automation/morning-brief/output/`, so it survives across daily runs regardless of
which container executes them. **First action of every run:** clone/pull
`nonsobeam/nonsobeam`, check out this branch, and read everything under
`automation/morning-brief/state/` before doing anything else. **Last action of every
run:** `git add`, commit, and push the updated state and the day's output file back
to this same branch — a run that doesn't persist its state defeats the entire point
of this rewrite.

## 0. CONFIG

```
OWNER_NAME        = Thaddeus Uzornne
OWNER_EMAIL       = Thaddeus.Uzornne@wetransact.io
OWNER_ROLE        = Customer Success Manager at WeTransact
TIMEZONE          = Europe/Lisbon
WORKING_HOURS     = Mon-Fri 09:00-18:00 local
SLA_TARGET_HOURS  = 9
LOOKBACK_DAYS     = 14        # only used for the SLA scorecard window and full-resync fallback
STATE_DIR         = automation/morning-brief/state   (inside nonsobeam/nonsobeam, this branch)
OUTPUT_DIR        = automation/morning-brief/output  (inside nonsobeam/nonsobeam, this branch)
MAIL_METHOD       = none      # explicitly disabled by Thaddeus — report only, never send mail
BUILD_DASHBOARD   = false
```

Everything in the original v2 spec — Sections 1, 2, and 4 through 16 — still applies
unchanged. This document only replaces Section 3. Read the full v2 spec (it's in this
repo's history / conversation record) for those sections if you need the exact
wording; the summary below is enough to run the task correctly.

## 3. READ PASS — INCREMENTAL (replaces v2 Section 3)

**First action, before anything else:** `TZ="Europe/Lisbon" date`. That is "now". Do
not use any date from context.

**Second action:** clone or pull `nonsobeam/nonsobeam`, check out this branch, and
read `automation/morning-brief/state/history.jsonl`. Find the `last_run_at` of the
most recent line (fall back to that line's `date` at 23:59 local if `last_run_at`
isn't present in an older entry).

### Decide full vs incremental

- **No history.jsonl, or it's empty, or the last run was more than `LOOKBACK_DAYS`
  days ago, or any required state file under `state/` is missing or unreadable:**
  do a **full read** exactly as v2 Section 3 describes — Inbox and Sent Items, full
  `LOOKBACK_DAYS` window, fully paginated, both folders. This is the resync path;
  it should be rare (first run, or recovery after a gap).
- **Otherwise:** do an **incremental read** — Inbox and Sent Items, `afterDateTime`
  = the prior run's `last_run_at` (not `LOOKBACK_DAYS` ago), fully paginated. This
  is almost always a small number of messages (typically well under a page), so
  page through all of it without a subagent — no need to delegate a read this
  small.

Either way, page fully — `folderName` "Inbox" and "Sent Items", newest first,
follow `nextOffset` until `moreResults` is false. Include CC. Convert all
timestamps from UTC before any arithmetic (Lisbon is UTC+1 WEST from the last
Sunday in March to the last Sunday in October, UTC+0 WET otherwise).

### Reconstitute the full SLA window without refetching it

The SLA scorecard still covers the full `LOOKBACK_DAYS` window even on an
incremental day. Build that view like this, with **no extra Outlook calls**:

1. Load every thread object from `state/threads-latest.json` (the previous run's
   output). Drop any whose `anchor_at` is now older than `LOOKBACK_DAYS` — they've
   aged out of the window.
2. For every thread still in scope with `sla_status` other than `Replied` or
   `No reply needed` (i.e. still genuinely open), **recompute
   `working_hours_elapsed` and `sla_status` using its stored `anchor_at` against
   the new "now"** — pure arithmetic, the same business-hours function as v2
   Section 8. A thread's breach clock keeps ticking even on days nothing new
   arrives on it; that recomputation is why open threads can flip from `Awaiting
   reply` to `Breached` between runs with zero new reads.
3. Merge in whatever the incremental read just found:
   - A new inbound message on an existing open thread's `thread_key` → it doesn't
     change the anchor (the anchor is still the original unanswered message,
     unless this new one is itself a fresh ask — use judgement same as v2 Section
     4's anchor rule) but may be new information worth surfacing.
   - A new inbound message that doesn't match any existing thread → classify it
     fresh per v2 Section 4 and add it as a new thread object.
   - A new **sent** message matching an open thread's `thread_key` and dated after
     that thread's `anchor_at` → that thread just got its first reply. Compute
     `working_hours_elapsed` from `anchor_at` to this reply, set `sla_status` =
     `Replied`, `replied_at` = this message's timestamp.
4. Threads that were already `Replied` or `No reply needed` in the prior run stay
   exactly as they were — never recompute a status that's already settled; only
   genuinely open threads move. See Section 4a for what `No reply needed` means
   and when to use it.

Everything downstream (Sections 4 through 16 of the v2 spec — classification,
people/availability, commitments, carry-forward, ranking, the email, delivery)
runs exactly as before, just against this reconstituted thread set instead of a
freshly re-read one.

### At the end of every run

Write `threads-latest.json` (the reconstituted-plus-merged set), append one line
to `history.jsonl` (include `last_run_at` = the "now" this run captured at the
top, not just `date`, so the next run knows precisely where to resume), update
`carry-forward.json`, `commitments.json`, `people.json` as v2 describes, write
`output/brief-YYYY-MM-DD.html`, then **commit and push all of it to this branch**
before ending the run. A run whose state never lands back in git means tomorrow's
run silently falls back to a full resync — not a failure, just wasted tokens, so
don't skip this step.

**Verify the push actually landed before ending the run** — run `git log
origin/<this-branch> --oneline -1` (fetching first) and confirm it shows the
commit you just made. Don't just assume `git push` printing success means it's
really there. If the push failed or the commit is missing, retry once; if it
still isn't there, say so explicitly in the brief itself (top of Heads-up) rather
than ending quietly — a run that silently didn't persist is worse than one that
flags its own failure. (This check exists because a manual test run on 2026-08-24
completed and apparently sent a notification but never actually committed
anything — don't repeat that silently.)

## 4a. CLASSIFICATION GUARDRAILS (added 2026-08-28, amends v2 Section 4/9)

On 2026-08-28 Thaddeus reviewed the six threads shown as breached and said directly:
**none of them required a reply.** Investigation confirmed all six — the classifier had
been systematically over-escalating. Root causes, and the fixes that now apply to every
run, going forward:

- **`[OWNER AWAY]` is not a green light to escalate to Thaddeus.** A thread addressed to
  another colleague (e.g. Geoffroy) who happens to be away does **not** automatically
  become Thaddeus's to answer just because he's cc'd or a co-recipient. Before tagging
  `[OWNER AWAY]` and assigning `next_step_owner: Thaddeus`, check:
  1. Is the message actually addressed/directed to Thaddeus (to-line, or "Thaddeus,"
     opening), or is he only cc'd while someone else is the addressee?
  2. Is there evidence someone else is already handling it through another channel —
     a recurring call-booking pattern, a co-recipient who owns the account relationship,
     an internal thread showing someone else picked it up? If so, that's the owner, not
     Thaddeus, regardless of who's out.
  3. If it's genuinely only Thaddeus who can act and no one else is covering it, the
     escalation is valid — but say so explicitly with the specific evidence, not just
     "Geoffroy is out."
- **FYI / churn / close-out / casual-aside messages are not open asks.** A message that
  states a decision or fact and doesn't pose a question (e.g. "we will no longer be
  using this tenant," a casual aside forwarding an automated notification with no direct
  ask) does not need a reply just because it's unread and inbound. Before marking a
  thread `Breached`/`Awaiting reply`, confirm the message actually contains an
  unanswered question or explicit ask directed at Thaddeus. If it doesn't, classify it
  `No reply needed` (see below) instead of letting the SLA clock run on it.
- **Check for a superseding thread before tracking an old one as independently
  breached.** If a different, more recent thread on the same real-world issue shows
  active back-and-forth already resolving it, the older thread is superseded, not a
  separate open item — don't count both.
- **A new terminal `sla_status` value: `"No reply needed"`.** Use it (with a
  `no_reply_reason` string field explaining why) for threads that fail the checks
  above, or that Thaddeus has explicitly told you don't need a reply. Treat it exactly
  like `Replied` in the reconstitution logic (Section 3): never recompute it on a later
  run, never count it toward `breached`/`still_open`/`threads_needing_reply` in the SLA
  scorecard, and never increment its `times_surfaced` in `carry-forward.json` — it's
  settled, not carried forward. This is distinct from `state/no-reply-needed.json`,
  which stays Thaddeus's own hand-edited file (never write to it); this is the
  automation's own record of threads it determined, or was told, don't need action.
- When in doubt between escalating and not, prefer surfacing the evidence in the brief
  (e.g. "addressed to Geoffroy, not clearly Thaddeus's — flagging for a decision") over
  silently assigning `next_step_owner: Thaddeus` and starting the SLA clock.

## Everything else

Sections 1 (who/where), 2 (state file table — now rooted at the repo path above),
4 (classification), 5 (people/availability), 6 (commitments), 7 (calendar/Teams/
portal enrichment), 8 (SLA computation — same business-hours Python, same
assertions), 9 (carry-forward), 10 (ranking into MIT/three/five/backlog), 11 (the
brief's content and format — read as "the report", not "the email"; the anatomy,
sections, and Outlook-safe HTML formatting rules still apply, they just describe a
file now), 14 (guardrails), and 15 (operational rules) are unchanged from the v2
spec, **except Section 12 (delivery), which is fully replaced by the rule below.**

**Delivery — report only, never email. This is an explicit, standing instruction
from Thaddeus, not a workaround for the 403 below — do not revert to emailing even
if `Mail.Send` gets consented later.**

- **Never call `outlook_send_mail`.** Don't try it "just to check" — skip it
  entirely. (For the record: it was returning `403 FORBIDDEN` — the connector's
  `Mail.Send` permission wasn't consented — but that's no longer the reason it's
  skipped; it's skipped because Thaddeus asked for report-only delivery.)
- Write the brief to `output/brief-YYYY-MM-DD.html` and commit + push it, every
  run, same as always — that's the durable copy.
- Send it to Thaddeus as a file in this session (proactive) each run, so it's
  sitting there as an attachment, not just prose in the transcript.
- **Then call the push-notification tool** (status "proactive") with a short,
  concrete one-liner — e.g. `"Morning brief ready: 1 MIT, 3 breached, 8 tasks"`
  — pulling the actual numbers from this run, not a generic "brief ready"
  message. This trigger is self-bound (fires into this same session rather than
  a fresh one), so the create_trigger notification setting doesn't apply here;
  this explicit call is what actually reaches his phone/desktop each morning.
  Do this every run, even a quiet one — a quiet day's notification just has
  smaller numbers in it, it isn't skipped.
- Everything else in Section 12 (writing the plain-markdown copy to stdout,
  the 403-vs-429 handling for any *other* Graph calls in the run) still applies.
