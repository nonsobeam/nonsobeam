# Email SLA scan — one-shot, local, 11 months

A standalone scan of an **Outlook export** that works out how quickly you reply
to mail that needs a reply, and writes a single self-contained HTML dashboard.

This replaces the scheduled 14-day Cowork task with something you run yourself,
on your own machine, over a much longer window. It is **not** the scheduled
automation in `../morning-brief/` — that one still runs twice weekly against the
live mailbox. This one touches no mailbox and sends no mail.

## What it costs

Nothing, by default. There is **no model call in a default run** — classification
is done by rule, offline. On the 1,320-message test fixture the rules settled
every message with nothing left over. On a real mailbox expect a small ambiguous
tail; those are counted as needing a reply (missing an obligation costs more than
an extra row you dismiss by hand) unless you opt into `--llm`.

That was the point of the exercise: the cheapest model call is the one you don't
make. See [Optional LLM pass](#optional-llm-pass) before spending anything.

## Quick start

```bash
cd automation/email-sla
cp config.example.json config.json     # then set "me" to your address(es)

# 1. Export from Outlook (see below), then:
python3 run.py --export ~/exports/mailbox.pst

# or, if your export produced one file per folder:
python3 run.py --inbox ~/exports/Inbox.mbox --sent ~/exports/Sent.mbox
```

The dashboard lands at `out/sla-dashboard.html`. Open it in a browser.

Python 3.11+. A default run needs no third-party packages at all; `.pst` support
needs `libpff-python` (see `requirements.txt`).

## Getting the export

**You must export Sent Items as well as Inbox.** Sent mail is the only way to
know what you already answered — without it every thread looks unanswered and
the figures are meaningless. The run warns loudly if Sent Items is empty.

- **Outlook for Windows** — File → Open & Export → Import/Export → Export to a
  file → Outlook Data File (.pst). Select Inbox and Sent Items, include
  subfolders. One `.pst` holds both, so pass it to `--export`.
- **Outlook for Mac** — File → Export → Outlook for Mac Data File (.olm). Also
  one file: pass it to `--export`.
- **Anything producing mbox or .eml** — pass `--inbox` and `--sent` separately,
  since those formats carry no folder of their own.

Exports contain your whole mailbox. `data/` and `out/` are gitignored so nothing
from them can be committed by accident; keep the export itself outside the repo.

## The SLA rule

Every mail needing a reply should get one inside **9 working hours**, where
working hours are **Monday to Friday, 09:00–18:00 Europe/Lisbon** and nothing
else. Nine working hours is exactly one working day: a mail arriving Tuesday
15:00 is due Wednesday 15:00; one arriving Friday 16:00 is due Monday 16:00. Mail
landing out of hours starts its clock at 09:00 the next working day.

Outlook stores UTC. Lisbon is UTC+1 in summer (WEST) and UTC+0 in winter (WET),
so conversion goes through `zoneinfo` rather than a hardcoded offset — a fixed
offset silently shifts every timestamp for half the year.

### The anchor rule

One row per thread, always. For each thread the **anchor** is the most recent
inbound message that needed a reply. The clock runs from that message to your
first reply *after it*, or to now if there is none. An earlier reply in the same
thread does not count — it predates the thing you now owe an answer to.

### Statuses

| Status | Meaning |
|---|---|
| **Replied** | answered inside 9 working hours |
| **Breached** | passed 9 working hours, whether or not it has since been answered |
| **Awaiting reply** | still open, still under 9 working hours |

"No reply needed" is yours to set by hand. The scan never assigns it.

## How classification works

A cascade, cheapest first:

1. **Deterministic rules** — headers and sender patterns that settle it outright.
   `List-Unsubscribe` means newsletter; `Auto-Submitted` means robot; a
   `urn:content-classes:calendarmessage` content class means calendar traffic.
   Free, offline, reproducible.
2. **Heuristics** — for ordinary human mail: does it actually ask you for
   something? Question marks, request verbs, being on To: rather than Cc:.
3. **Optional LLM** — only whatever is still genuinely ambiguous.

Automated mail that creates a real obligation is deliberately **kept**: signature
requests, entity verifications, organisation invitations, access grants. These
look like noise and are not — only you can click them.

Every verdict carries the rule that produced it, shown in the Note column of the
full thread table, so you can argue with any row.

## Reconciliation

The figures are asserted before anything renders:

- replied-in-target + breached + still-open must equal threads-needing-reply
- the average must equal total reply hours ÷ count of replied threads
- per-week counts must sum to the overall counts
- open threads must not have leaked into the average

If any check fails the run exits non-zero and renders nothing, rather than
shipping a confident dashboard full of wrong numbers.

Open threads have **no reply time** and are never averaged in as zero. The
replied population and the needing-reply population are different sizes; every
stat tile says which one it is built from.

## Optional LLM pass

Off by default, and the pipeline is complete without it. Enable it only if you
want a model to arbitrate the ambiguous tail:

```bash
cp llm-env.sample .env       # fill in base URL, key, model
python3 run.py --probe-llm   # confirm the key and endpoint first
python3 run.py --export ~/exports/mailbox.pst --llm
```

It speaks the OpenAI chat-completions shape, so it points at any compatible
endpoint. Only subject, sender and a short preview are sent, batched 40 at a
time — that keeps both cost and data exposure down. The key is read from the
environment only: never from config, never logged, never committed.

**A note on the CompactifAI / Multiverse key.** `--probe-llm` exists because the
key you have (prefix `wtk_`) does not match any documented CompactifAI key
format, and `wtk_` looks more like a WeTransact credential than a Multiverse one.
Probe before you build anything on it. If the probe returns 401, it is the wrong
credential — do not retry it against other endpoints.

## Commands

```bash
python3 run.py --export FILE            # .pst or .olm, both folders in one file
python3 run.py --inbox A --sent B       # mbox or .eml directories
python3 run.py --months 11              # window length (default: config)
python3 run.py --now 2026-08-29T09:00   # pin "now", for reproducible runs
python3 run.py --dump data/parsed.jsonl # also write the parsed messages
python3 run.py --llm                    # opt into the model pass
python3 run.py --probe-llm              # list models at the endpoint, then exit

python3 tests/test_workinghours.py      # the clock
python3 tests/test_pipeline.py          # threading, anchor rule, reconciliation
python3 tests/make_fixture.py --out data/fixture   # synthetic mailbox
python3 tests/shoot.py --scheme dark    # screenshot the dashboard
```

## Layout

| Path | What it is |
|---|---|
| `run.py` | CLI orchestrator |
| `src/workinghours.py` | the Mon–Fri 09:00–18:00 Lisbon clock |
| `src/schema.py` | the normalised message record; subject/address normalisation |
| `src/mailreaders.py` | PST, OLM, mbox and .eml readers |
| `src/classify.py` | the rules cascade |
| `src/threads.py` | thread grouping and the anchor rule |
| `src/sla.py` | metrics, weekly breakdown, reconciliation |
| `src/render.py` | the HTML dashboard |
| `src/llm.py` | optional OpenAI-compatible classifier |
| `tests/` | tests, synthetic fixture generator, screenshot tool |

`config.json`, `.env`, `data/` and `out/` are gitignored.

## What this does not do

By design, and deliberately unlike the scheduled task it replaces:

- **Sends no mail.** No report email, no replies, no drafts.
- **Touches no mailbox.** It reads a local file. Nothing is moved, labelled,
  archived or deleted, because it has no connection to move anything with.
- **Writes nowhere else.** No Notion, no OneDrive, no SharePoint. One HTML file.

## Known limits

- **Threading is subject + participant overlap**, not conversation ID, so it
  holds for exports that drop that field. Two unrelated threads with the same
  subject *and* an overlapping participant would merge; in practice rare.
- **Long windows accumulate dead threads.** Over 11 months most "open breaches"
  are threads that died months ago, not things still waiting on you. The
  open-breach tile splits the last 30 days from the rest for that reason.
- **Account mapping is opt-in.** Anything not in `config.json`'s `accounts` map
  renders as "not identified". The scan never guesses an account name.
- **The rules were written against a synthetic fixture.** They handle that
  mailbox completely, which is a weaker claim than handling yours completely.
  Check the Note column on a real run and tune the patterns in `classify.py`.
