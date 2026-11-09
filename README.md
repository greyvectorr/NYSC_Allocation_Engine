# 🇳🇬 NYSC Posting Allocation Engine

> A rule-driven, crash-proof Python engine that reads messy corps-member records, validates every one, and posts each valid member to a Nigerian state, **never their own state of origin** and never beyond a state's capacity.

![Python](https://img.shields.io/badge/python-3.x-blue) ![Dependencies](https://img.shields.io/badge/dependencies-none%20(stdlib%20only)-brightgreen) ![Status](https://img.shields.io/badge/status-prototype-orange)

---

## Table of contents

1. [Background and motivation](#1-background-and-motivation)
2. [Objectives](#2-objectives)
3. [Features at a glance](#3-features-at-a-glance)
4. [Architecture](#4-architecture)
5. [Quick start](#5-quick-start)
6. [Input format and validation rules](#6-input-format-and-validation-rules)
7. [Configuration reference](#7-configuration-reference)
8. [The allocation algorithm](#8-the-allocation-algorithm)
9. [State-name resolution (typo tolerance)](#9-state-name-resolution-typo-tolerance)
10. [Error handling: how it never crashes](#10-error-handling-how-it-never-crashes)
11. [Outputs](#11-outputs)
12. [Testing and results](#12-testing-and-results)
13. [Design decisions and lessons learned](#13-design-decisions-and-lessons-learned)
14. [Known limitations](#14-known-limitations)
15. [Roadmap](#15-roadmap)
16. [Data, privacy and ethics](#16-data-privacy-and-ethics)
17. [Contributing, license and credits](#17-contributing-license-and-credits)

---

## 1. Background and motivation

Every year the **National Youth Service Corps (NYSC)** posts tens of thousands of fresh Nigerian graduates to states across the federation for a one-year service. The golden rule is simple and well known: **you are never posted to your own state of origin**.

Allocating at that scale is hard. Records arrive as messy human-entered text: missing fields, mangled call-up numbers, nonsense phone numbers, misspelt state names, even fictional origins like "Wakanda". A good allocation system has to:

- survive bad data without losing the whole batch,
- apply the posting rules consistently and transparently,
- keep an audit trail of every decision, and
- take its rules from configuration, not hard-coded constants, so that policy changes don't require code changes.

This project began as a capstone exercise (a starter file with stubbed functions covering **file handling, regular expressions, exception handling and JSON configuration**) and was then extended into a hardened engine. The ambition behind it is a solution that could eventually support allocation work nationwide. This repository is an honest **prototype / supplementary solution**: it demonstrates the rule engine, validation and auditing end to end on sample data, and [Known limitations](#14-known-limitations) and the [Roadmap](#15-roadmap) say plainly what a national-scale system would still need.

---

## 2. Objectives

### Functional objectives

| # | Objective | Status |
|---|-----------|--------|
| 1 | Load posting rules from a JSON config (`posting_config.json`) | ✅ |
| 2 | Read corps-member records from a free-text file | ✅ |
| 3 | Parse each record with **regex** and validate name, call-up number, origin, course and phone | ✅ |
| 4 | Allocate each valid member to a state that is **not their origin** and **still has a free slot** | ✅ |
| 5 | Use the 36 states of Nigeria for allocation (FCT optional via config) | ✅ |
| 6 | Write a human-readable report, a JSON file, and an **append-only** audit log | ✅ |
| 7 | Reject every bad record with a **clear reason**, and always print a run summary | ✅ |
| 8 | **Never crash**, however broken the input is | ✅ (no crash found in testing, see [section 12](#12-testing-and-results)) |

### Stretch goals (all implemented)

| Stretch goal | How it was done |
|--------------|-----------------|
| Course-of-study prioritisation | Priority courses (Medicine, Education, Agriculture, and more) go to the state with the **most room left**, spreading scarce skills |
| Capacity warnings | States left with fewer than `min_capacity_warning` slots are flagged ⚠️ in the summary |
| Redeployment appeals | A second pass re-posts members who found no state, using **swaps** with already-posted members, or reports clearly that the batch is over capacity |
| Call-up letters | One mock letter per posted member in `letters/` |

### Beyond the brief

Duplicate call-up detection, Nigerian phone normalisation (`+234…`), **typo-tolerant state names**, atomic file writes, a timestamped and run-tagged audit trail, a tolerant parser (CRLF line endings, any letter case, odd spacing), and safe fallbacks for a missing or corrupt config.

### Learning objectives

- Build a multi-stage pipeline (load → read → split → parse → validate → allocate → output → summarise).
- Read text and JSON, and write three kinds of output (report, JSON, append-only log).
- Use regex both to **extract** fields from free text and to **validate** their format (`re.fullmatch`, not `re.search`).
- Design a **custom exception hierarchy** so one bad record is logged and skipped, not fatal.
- Implement **rule-based logic** that reads its rules from configuration.

---

## 3. Features at a glance

- **Golden rule enforced:** nobody is ever posted to their own state of origin.
- **Capacity-aware:** slot counts per state are tracked across the whole batch and never exceeded.
- **Config-driven:** states, slots, priority courses, strategy, warning threshold and letter generation all live in `posting_config.json`.
- **Strict validation with human-readable reasons:** every rejection says exactly what is wrong.
- **Typo-tolerant states:** `Anambara` is read as `Anambra`; ambiguous or unknown names are rejected with a hint, never guessed.
- **Smart allocation:** priority courses are spread across states; two strategies for everyone else.
- **Appeals pass:** swap-based redeployment for members who initially found no state.
- **Fail-soft everywhere:** broken config, missing files, bad records, unwritable outputs and odd consoles never stop the run.
- **Full audit trail:** every event is appended, timestamped, and tagged with a run ID.
- **Zero dependencies:** the Python standard library only.

---

## 4. Architecture

The program is a pipeline. Data flows through these stages:

```
posting_config.json ─┐
                     ├─► LOAD ─► READ ─► SPLIT ─► PARSE ─► VALIDATE ─► ALLOCATE ─► APPEAL ─► OUTPUT ─► SUMMARY
corps_members.txt ───┘                            (regex)   (rules)     (config)    (swaps)   (files)   (finally)
```

### Repository layout

```
.
├── engine.py               # the whole engine (single file, stdlib only)
├── posting_config.json     # posting rules
├── corps_members.txt       # sample input (fictional people)
├── README.md
│
└── generated on each run
    ├── posting_report.txt  # human-readable report
    ├── postings.json       # machine-readable postings
    ├── audit.log           # append-only event trail (grows across runs)
    └── letters/            # one mock call-up letter per posted member
        └── NYSC_2026_A_0451.txt
```

> 💡 Consider adding the generated files to `.gitignore`. They change on every run.

All paths are **script-relative** (`BASE_DIR`), so the engine finds its files no matter which directory you launch it from.

### Function map

| Stage | Functions | Responsibility |
|-------|-----------|----------------|
| **0. Exceptions** | `PostingError` → `MalformedRecordError` → `DuplicateRecordError`; `PostingError` → `NoAvailableStateError` | Describe exactly what went wrong |
| **1. Load** | `default_config`, `normalize_config`, `load_config`, `read_records` | Read the rules and records safely; repair or default anything broken |
| **2. Split/Parse** | `split_records`, `_extract`, `peek_name`, `normalize_phone`, `parse_record` | Regex extraction and validation |
| **State names** | `_state_key`, `_edit_distance`, `resolve_state`, `canonical_state` | Exact, alias and typo-tolerant state resolution |
| **3. Allocate** | `allocate_state`, `try_redeploy`, `_priority_courses` | Rule-based placement and swap appeals |
| **4. Output** | `write_posting_report`, `write_postings_json`, `append_audit`, `write_call_up_letters`, `_atomic_write`, `_guard` | Safe file writing |
| **5. Orchestrate** | `process_members`, `print_summary`, `main` | `try` / `except PostingError` / `finally` loop, counters, summary |
| **Utilities** | `say`, `_now`, `_to_nonneg_int` | Crash-proof printing, timestamps, safe int conversion |

---

## 5. Quick start

### Requirements

- Python 3 (developed and tested on Python 3.12)
- No third-party packages

### Run it

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
python engine.py
```

### Example output

This is the real output of the shipped `engine.py`, `posting_config.json` and `corps_members.txt` (the low-capacity list is shortened here):

```
=== NYSC Posting Allocation Engine ===
⚠️  CONFIG: bad or missing slot count for FCT - treating as 0
Loaded 37 available states. Reading records...

POSTED    ✅  Chinedu Okafor (Anambra, Medicine) -> Lagos
POSTED    ✅  Aisha Bello (Kano, Education) -> Kaduna
POSTED    ✅  Emeka Nwosu (Enugu, Agriculture) -> Kano
POSTED    ✅  Fatima Sani (Sokoto, Engineering) -> Lagos
POSTED    ✅  Tunde Adeyemi (Lagos, Law) -> Rivers
REJECTED  🛑  Ngozi Eze : missing required field 'origin'
REJECTED  🛑  Ibrahim Musa : invalid call-up number '2026-IB-99'
REJECTED  🛑  Blessing Johnson : invalid phone number '12345'
REJECTED  🛑  Yakubu Danjuma : 'Wakanda' is not a Nigerian state
POSTED    ✅  Chioma Okeke (Imo, Education) -> Akwa Ibom
REJECTED  🛑  <blank record> : missing required fields: 'name', 'callup', 'origin', 'course', 'phone'
POSTED    ✅  Suleiman Abdullahi (Katsina, Medicine) -> Benue

Wrote: posting_report.txt, postings.json  (and appended to audit.log); 7 call-up letter(s) in letters/

============================================================
📊 RUN SUMMARY
   Records read : 12
   Posted       : 7
   Rejected     : 5
   Free slots left in total: 104
   Remaining slots:
     Abia 2 | Adamawa 3 | Akwa Ibom 3 | Anambra 3 | Bauchi 3 | Bayelsa 2 |
     Benue 3 | Borno 3 | Cross River 3 | Delta 4 | Ebonyi 2 | Edo 3 | ...
   ⚠️  Low capacity (fewer than 10 slots left): Abia, Adamawa, Akwa Ibom, ...
```

Two things in that output are explained in [section 7](#7-configuration-reference): the FCT warning (FCT is listed as a destination but has no slot count) and why every state is flagged as low capacity (the threshold is higher than any state's slot count).

The exit code is `0` even when records are rejected. Rejections are expected outcomes, not failures.

---

## 6. Input format and validation rules

### Record format

Records are separated by `=== CORPS MEMBER ===` and contain `LABEL: value` lines:

```
=== CORPS MEMBER ===
NAME: Chinedu Okafor
CALLUP: NYSC/2026/A/0451
ORIGIN: Anambra
COURSE: Medicine
PHONE: 08031234567
```

The parser is deliberately forgiving about *form* and strict about *content*:

- Labels and the record marker are case-insensitive; spacing is flexible; `CALLUP`, `CALL-UP` and `CALL_UP` all work.
- Windows (`\r\n`) and Unix line endings both work.
- A **blank block is not silently dropped.** It reaches the parser and is rejected with a reason, as the brief requires.
- Stray text before the first marker is treated as a (bad) record and rejected, not ignored.
- The text before the first marker is dropped only if it is empty.

### Validation rules

| Field | Rule | Example rejection message |
|-------|------|---------------------------|
| **All fields** | Every one of `name`, `callup`, `origin`, `course`, `phone` must be present and non-empty. If several are missing, all are listed. | `missing required field 'origin'` |
| **name** | 2–80 characters; letters, spaces and `. ' ’ -` only; no digits | `invalid name 'Num3ric'` |
| **callup** | `re.fullmatch(r"NYSC/\d{4}/[A-Z]/\d{4}")` after upper-casing, e.g. `NYSC/2026/A/0451` | `invalid call-up number '2026-IB-99'` |
| **origin** | Must resolve to a real state or FCT ([section 9](#9-state-name-resolution-typo-tolerance)) | `'Wakanda' is not a Nigerian state` |
| **course** | 2–80 characters; no digits | `invalid course 'Law101'` |
| **phone** | Normalised first (`+234…`/`234…` → `0…`; spaces, dots, dashes, brackets stripped), then `re.fullmatch(r"0[789][01]\d{8}")`, i.e. `0` plus 10 digits with a real mobile prefix | `invalid phone number '12345'` |
| **duplicates** | The same call-up number twice in a batch: the second is rejected | `duplicate call-up number 'NYSC/2026/A/0001'` |

`re.fullmatch` is used rather than `re.search` for format checks, so `xxNYSC/2026/A/0451yy` does not sneak through.

---

## 7. Configuration reference

All rules live in `posting_config.json`. A cleaning step (`normalize_config`) repairs anything odd and **warns instead of crashing**.

```json
{
  "states_of_Nigeria": ["Abia", "Adamawa", "...", "Zamfara"],
  "slots_per_state": { "Abia": 2, "Adamawa": 3, "...": 0, "Zamfara": 2 },
  "course_of_study": ["Medicine", "Nursing", "Pharmacy", "Medical Laboratory Science",
                      "Education", "Agriculture", "Engineering", "Computer Science"],
  "min_capacity_warning": 10,
  "regular_strategy": "balanced",
  "generate_call_up_letters": true
}
```

| Key | Type | Meaning | Behaviour when wrong or missing |
|-----|------|---------|---------------------------------|
| `states_of_Nigeria` | list of strings | Destination states, **in priority order** (order matters for ties and `first_fit`). Add `"FCT"` to allow postings there. | Typos are corrected; unknown entries are ignored with a warning; if nothing usable remains, the 36 default states are used. The legacy key `available_states` is also accepted. |
| `slots_per_state` | object | State → number of slots | A missing, negative or non-numeric count becomes `0` with a warning. If the whole object is missing, **every state gets 100 slots**. State keys may also be misspelt (they are resolved the same way). |
| `course_of_study` | list | **Priority courses** (matched case-insensitively). A dict is also accepted (its keys are used). The legacy key `priority_disciplines` also works. | Falls back to the built-in priority list |
| `min_capacity_warning` | integer ≥ 0 | A state is flagged ⚠️ in the summary when its remaining slots are **fewer than** this number | Default `10` |
| `regular_strategy` | `"first_fit"` or `"balanced"` | How non-priority courses are placed ([section 8](#8-the-allocation-algorithm)) | Anything else becomes `first_fit` |
| `generate_call_up_letters` | boolean | Write per-member letters into `letters/` | Default `true` |

### Notes on the shipped configuration

- **FCT has no slot count.** `"FCT"` appears in `states_of_Nigeria`, but `slots_per_state` has no `"FCT"` entry, so it gets 0 slots and the engine warns on every run. Nobody is posted to FCT until you add an entry such as `"FCT": 4`. (FCT is always accepted as a valid *origin* regardless.)
- **`min_capacity_warning: 10` flags every state.** With 2–6 slots per state, every state is always below 10, so the warning carries no information. Choose a value that fits your slot sizes (the original sample used `2`).
- **The slot numbers are illustrative** (2–6 per state). Replace them with real figures.

---

## 8. The allocation algorithm

For each valid member, `allocate_state` applies these rules:

1. **Eligible states** = states in config order that are **not the member's origin** and have **more than 0 slots remaining**.
2. If there are none, raise `NoAvailableStateError`.
3. **Choose** a state:
   - **Priority course** (listed in `course_of_study`), or `regular_strategy == "balanced"`: pick the eligible state with the **most remaining slots**. Ties go to the earlier state in config order.
   - Otherwise (`first_fit`): pick the **first** eligible state in config order.
4. **Decrement** that state's remaining slots, then return the state.

Slot counts live in a **copy** of the config (`dict(config["slots_per_state"])`) that is updated across the whole batch, so the config itself is never mutated.

### Which strategy should I use?

| Strategy | Behaviour | Trade-off |
|----------|-----------|-----------|
| `first_fit` | Fills states in config order (Abia first, and so on) | Simple and predictable, but concentrates people in early states |
| `balanced` | Always uses the state with most room | Spreads people evenly; ties still follow config order |

Priority courses are **always** spread, whichever strategy you choose.

### Redeployment appeals (swap pass)

Slots never come back, so simply retrying a failed member is pointless. But a **swap** can work. After the main pass, each member who got `NoAvailableStateError` is given one appeal:

> The only free slots left are in states the member can't use (their own origin). If an **already-posted member from elsewhere** can move into that free slot, the appellant takes the slot that member vacates.

Worked example (2 states: Lagos with 1 slot, Kano with 2):

| Step | Event | Lagos | Kano |
|------|-------|-------|------|
| 1 | Imo One → Lagos | 0 | 2 |
| 2 | Imo Two → Kano | 0 | 1 |
| 3 | Kano Three (origin Kano): Lagos full, Kano is their origin → **no state** | 0 | 1 |
| 4 | **Appeal:** Imo One moves Lagos → Kano; Kano Three takes Lagos | 0 | 0 |

Result: everyone is placed, no one is in their own origin, no state is over capacity. If no swap exists, the member stays rejected and the engine reports that **the batch is over capacity**. Every outcome is audited.

### Invariants

By construction, and verified in testing: nobody is posted to their origin; no state exceeds its slots; each call-up number is posted at most once; swaps preserve all of the above.

---

## 9. State-name resolution (typo tolerance)

Humans misspell state names, and rejecting `Anambara` as "not a state" would be wrong. But a wrong auto-correction could post someone **into their own origin**, breaking the golden rule. So resolution is deliberately conservative. `resolve_state(text)` returns `(state, status, options)`:

| Status | Meaning | Example |
|--------|---------|---------|
| `exact` | Spelt correctly (case, spaces and hyphens ignored) or a known alias | `akwa-ibom` → Akwa Ibom; `Abuja` → FCT |
| `corrected` | Misspelt, but exactly **one** state is clearly closest | `Anambara` → Anambra |
| `ambiguous` | Too close to call between several states: **rejected with a hint** | `Ido` → *did you mean Edo or Imo or Ondo?* |
| `unknown` | Not a state at all: **rejected** | `Wakanda`, `Nigeria`, `Gotham` |

### How it works

1. **Normalise:** lowercase and keep letters only, so `Akwa-Ibom`, `AKWA IBOM` and `akwa ibom` become `akwaibom`.
2. **Exact match first.** This must happen before any distance maths. `Osun` is one letter from `Ogun`, so without this shortcut a perfectly spelt `Osun` would look ambiguous.
3. **Edit distance** (insert, delete, replace, or swap two neighbouring letters, each costing 1) against every official spelling and alias.
4. **Accept a correction only if:**
   - the best distance is within the allowance: **1 typo** for names of up to 7 letters, **2** for longer names; and
   - the best state beats the runner-up by a margin: **1** normally, **2** for short names (4 letters or fewer, like Imo, Edo, Oyo).
5. Names shorter than 3 letters are never fuzzy-matched.

**Aliases** (`_STATE_ALIASES`): `Abuja`, `FCT Abuja` and `Federal Capital Territory` map to FCT, and the common misspelling `Nassarawa` maps to Nasarawa.

**Transparency:** every correction is visible. The console and audit log show `[origin 'Anambara' read as 'Anambra']`, and `postings.json` keeps the original spelling in an `origin_as_written` field.

**Kill switch:** set `FUZZY_STATE_MATCHING = False` near the top of `engine.py` to accept only exact names and aliases.

### Safety check

Every single-letter typo (deletion, substitution, insertion, neighbour swap) of every official name was generated: **2,609 typos**. Results: **2,311 corrected to the right state, 298 rejected** (286 with a "did you mean" hint, the rest as unknown), and **0 corrected to the wrong state**.

---

## 10. Error handling: how it never crashes

### Custom exception hierarchy

```
Exception
└── PostingError                    # base: everything the engine knows how to handle
    ├── MalformedRecordError        # missing field, bad call-up/phone/origin/name/course
    │   └── DuplicateRecordError    # same call-up number twice
    └── NoAvailableStateError       # nothing fits for this member
```

A single `except PostingError as err:` catches all of these, and `str(err)` is the human-readable reason that goes into the console, report and audit log.

### Defence in depth

| Layer | What can go wrong | What happens |
|-------|-------------------|--------------|
| **Config** | File missing, unreadable, invalid JSON, wrong types, unknown states, bad slot counts | Warn, repair or fall back to built-in defaults |
| **Records file** | Missing, unreadable, binary garbage, odd encoding | Warn and carry on with an empty batch (`Records read: 0`); the file is read as UTF-8 with replacement for bad bytes |
| **Per record** | Any validation failure | `except PostingError`: reject, log, continue |
| **Per record, unexpected** | A genuine bug | `except Exception`: reject as `unexpected error (...)`, log, **continue** |
| **Appeals pass** | Bug or bad data in a swap | Guarded per member; the run continues |
| **Output files** | Disk full, permissions, a directory where a file should be | `_guard` reports the failure; other outputs still run. Writes go to a `.tmp` file and are swapped in with `os.replace`, so a crash never leaves a half-written file |
| **Audit log** | Unwritable | Warns **once** (not on every line) and carries on |
| **Console** | Emoji on a legacy Windows code page, closed pipe | `say()` falls back to ASCII; stdout is reconfigured to UTF-8 where possible |
| **`main()`** | Anything unforeseen; Ctrl-C | Last-line catch prints a clean message and logs the fault |
| **Summary** | Any failure above | Printed from a `finally` block, so it **always** appears |

---

## 11. Outputs

### `posting_report.txt`: for humans

```
======================================================================================
NATIONAL YOUTH SERVICE CORPS - POSTING REPORT
Generated: 2026-11-09T14:50:22
======================================================================================
Corps members posted: 7

  #  Name                    Call-up No.       Origin       Course             Posted to
--------------------------------------------------------------------------------------
  1  Chinedu Okafor          NYSC/2026/A/0451  Anambra      Medicine           Lagos
  2  Aisha Bello             NYSC/2026/B/1120  Kano         Education          Kaduna
  ...

======================================================================================
NOT POSTED (5)
======================================================================================
- Ngozi Eze: missing required field 'origin'
- Ibrahim Musa: invalid call-up number '2026-IB-99'
...
```

### `postings.json`: for machines

A list of member objects. Optional fields appear only when relevant:

```json
[
  {
    "name": "Chinedu Okafor",
    "callup": "NYSC/2026/A/0451",
    "origin": "Anambra",
    "course": "Medicine",
    "phone": "08031234567",
    "state": "Lagos"
  }
]
```

| Optional field | When it appears |
|----------------|-----------------|
| `origin_as_written` | The origin was misspelt and auto-corrected |
| `redeployed: true` | The member was placed by the appeals pass |
| `redeployed_from` | The member was **moved** to make room for an appellant |

### `audit.log`: append-only trail

Opened in append mode (`"a"`), so it grows across runs and is never overwritten. One line per event: `timestamp | run ID | event | details`.

```
2026-11-09T14:50:22 | RUN-20261109-145022 | RUN START | records=12 | states=37
2026-11-09T14:50:22 | RUN-20261109-145022 | POSTED | NYSC/2026/A/0451 | Chinedu Okafor | Anambra -> Lagos
2026-11-09T14:50:22 | RUN-20261109-145022 | REJECTED | Yakubu Danjuma | 'Wakanda' is not a Nigerian state
2026-11-09T14:50:22 | RUN-20261109-145022 | RUN END | read=12 posted=7 rejected=5
```

Event types: `RUN START`, `POSTED`, `REJECTED`, `REDEPLOYED`, `APPEAL FAILED`, `RUN END`, `ENGINE FAULT`. Messages are flattened to a single line.

### `letters/<callup>.txt`: mock call-up letters

One per posted member. The `/` in call-up numbers is replaced with `_` for the file name (`NYSC_2026_A_0451.txt`), because `/` is not allowed in file names.

```
NATIONAL YOUTH SERVICE CORPS
CALL-UP LETTER (MOCK)
========================================
Call-up No.: NYSC/2026/A/0451

Dear Chinedu Okafor,

Following the posting exercise, you have been posted to LAGOS STATE
for your one-year National Youth Service.
...
```

---

## 12. Testing and results

Testing was done with ad hoc scripts during development. They are not yet in the repository ([Roadmap](#15-roadmap)).

| Scenario | Result |
|----------|--------|
| Provided sample (12 records) | 7 posted, 5 rejected, each with the correct reason |
| No config file **and** no records file | Defaults used; `Records read: 0`; summary printed |
| Corrupt JSON config **and** random binary as records | No crash; defaults used; garbage rejected |
| Recoverable oddities (CRLF, lower-case labels and values, `+234 803 123 4567`, `0803-123-4567`, `akwa-ibom`, `Abuja`) | Parsed and accepted correctly |
| Duplicate call-up numbers, digits in a name, an empty field value, a 5,000-character name, text with no fields | Rejected cleanly; long values truncated in messages |
| Bad config values (unknown state, `"x"` as a slot count, non-numeric threshold) | Repaired with warnings |
| Swap appeal | Worked as in [section 8](#redeployment-appeals-swap-pass) |
| Over-capacity batch | Reported clearly; nobody over-allocated |
| Output paths blocked (directories where files should go) | Warnings only; the run completed |
| **Randomised fuzzing** (random deletions, insertions and substitutions, including NUL bytes and stray markers) | Three runs of 300, 200 and 200 mutated batches: **0 crashes, 0 stderr output, summary always printed** |
| **Typo sweep** over all state names | 2,609 typos, **0 wrong corrections** |

> "Never crashes" here means that **no crash was found** in this testing. It is a strong empirical result, not a formal proof.

---

## 13. Design decisions and lessons learned

- **Reject, don't guess, when in doubt.** Auto-correction is allowed only when one state is clearly closest. A wrong guess could violate the golden rule silently, while a rejection puts a human in the loop.
- **Exact match must come before fuzzy matching.** `Osun` and `Ogun` are one edit apart. Without an exact-match shortcut, a correct `Osun` is "ambiguous". An earlier refactor that removed the lookup table also removed this shortcut and the loop that filled the candidate list, so *every* state lookup crashed with `IndexError`. The lesson: code that looks redundant often carries several jobs, so remove it only with tests.
- **Fail soft at every boundary.** Anything that touches the outside world (files, JSON, console) reports the problem and continues with a safe fallback.
- **One try/except per record.** One bad record, or even a bug, can't stop a batch.
- **Blank records must reach the parser.** The brief requires them to be rejected with a reason, so `split_records` keeps them.
- **Atomic writes.** Write to `.tmp`, then `os.replace`, so readers never see a half-written file.
- **Config is data, code is mechanism.** Changing policy means editing JSON, not Python.
- **Loading never creates files.** `load_config` and `read_records` open files in read mode, which cannot create them. A missing input is reported, not invented. Only output files (`"w"` and `"a"` modes) are created by the engine.

### What changed from the course starter

| Starter | This version |
|---------|--------------|
| Stub functions returning placeholders | Fully implemented pipeline |
| `available_states`, `priority_disciplines` keys | `states_of_Nigeria`, `course_of_study` (old names still accepted) |
| Simple "first eligible state" rule | Priority spreading, a `balanced` strategy, a swap-based appeals pass |
| Exact state names only | Typo-tolerant resolution with safety margins |
| Config assumed valid | Normalised, repaired, with defaults |
| Plain writes | Atomic writes and guarded output |
| Plain-text audit lines | Timestamped, run-tagged events |

---

## 14. Known limitations

Being honest about scope matters, especially for something that touches real people's postings.

- **Prototype scale.** The records file is read into memory in one go. This is fine for tens of thousands of records, but it has **not been benchmarked at national scale**.
- **Greedy allocation.** Members are placed in file order, with a single-step swap appeal. This is not a globally optimal assignment. A bipartite-matching or flow-based approach would guarantee the maximum number of placements.
- **Real NYSC policy is not modelled.** There are no geopolitical-zone rules, exemptions, deferments, medical or marital considerations, or per-employer demand. Slot numbers are illustrative.
- **Auto-correction can't be perfect.** The ambiguity rules make wrong corrections rare (none in the sweep), but a name that is a typo of one state *and* a valid name of another is inherently ambiguous, and the engine can't know the writer's intent.
- **Heuristic validation.** The phone check enforces real mobile prefixes (`070/080/081/090…`), so newly issued prefixes would need a regex update. Name validation is permissive on purpose.
- **Plain files.** Text files in, text files out. There is no database, authentication or multi-user access.
- **Audit log is not tamper-evident.** It is append-only by convention (open mode), not by cryptographic guarantee.
- **No automated test suite in the repo yet.**

---

## 15. Roadmap

- [ ] **`pytest` suite**: turn the ad hoc scenarios and the fuzz and typo sweeps into repeatable tests.
- [ ] **CLI options**: `--config`, `--records`, `--init-config` (write a default config template on request), `--dry-run`, `--strict-states`.
- [ ] **Optimal allocation**: replace the greedy pass with min-cost flow or bipartite matching.
- [ ] **Policy rules**: geopolitical-zone constraints, exemptions and deferments, per-state demand by discipline.
- [ ] **Real inputs**: CSV, Excel and database import.
- [ ] **Persistence**: SQLite or PostgreSQL instead of flat files.
- [ ] **API and dashboard**: for example FastAPI plus a small web UI for reviewers.
- [ ] **Streaming** for very large batches.
- [ ] **Structured logging** (the `logging` module, rotation, hash-chained audit entries).
- [ ] **CI**: GitHub Actions running the tests on every push; type hints and linting.
- [ ] **Appeals UI**: let a human review ambiguous-state and over-capacity cases instead of auto-rejecting.

---

## 16. Data, privacy and ethics

- **All sample data is fictional.** Names, call-up numbers and phone numbers in `corps_members.txt` are made up.
- **Real records contain personal data.** If you adapt this for real use, handle it in line with Nigeria's data-protection law (the **Nigeria Data Protection Act, 2023**): collect the minimum, restrict access, and don't commit real data to a public repository.
- **Audit trail and fairness.** Every decision, including every rejection and its reason, is logged so outcomes can be reviewed. Any automated posting system should keep a **human review path** for rejected, ambiguous and over-capacity cases.

---

## 17. Contributing, license and credits

**Contributing.** Issues and pull requests are welcome. For code changes, please include a test that reproduces the behaviour you are fixing or adding, ideally one that tries to *break* the "never crashes" guarantee.

**License.** *Choose a license before publishing (for example MIT), and add a `LICENSE` file.*

**Credits.** Built as a capstone project (File Handling + Regular Expressions + Exception Handling + JSON configuration) and extended into a hardened allocation engine.
Author: *your name here*.
