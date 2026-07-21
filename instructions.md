# 🇳🇬 NYSC Posting Allocation Engine

> Thousands of fresh graduates are waiting for their call-up letters. You write the engine that decides where each one serves — and it must never crash.

**Difficulty:** ⭐⭐⭐⭐ (capstone — all topics) | **Estimated time:** ~4-5 hours | **Topics:** File Handling + Regular Expressions + Exception Handling (JSON config too)

## 📖 The Story

Every year, the National Youth Service Corps (NYSC) posts tens of thousands of fresh Nigerian graduates to states across the federation for their one-year service. The golden rule everybody knows: **you are never posted to your own state of origin** — a Lagos graduate might find themselves in Sokoto, a Kano graduate in Enugu.

You've been handed the batch of prospective corps members as a single messy text file. Some records are perfect. Others are missing their state of origin, carry a mangled call-up number, list a phone number that's clearly nonsense, or claim to hail from **"Wakanda."** One record is completely blank.

Your job is to build the **allocation engine**: read the records, validate every one, and post each valid corps member to a state that (a) isn't their origin and (b) still has an open slot — with the rules loaded from a config file. One bad record must **never** bring the whole batch down. Lives (well, service years) depend on it.

## 🎯 Learning Objectives

By the end of this capstone you will be able to:

- Build a small **multi-stage pipeline** (load → read → parse → validate → allocate → output → summarise).
- **Read** a text file and a **JSON** config, and **write** three kinds of output: a human report, a JSON file, and an **append-only** audit log.
- Use **regex** to pull structured fields (name, call-up number, phone, etc.) out of free text and to **validate** their format.
- Design and use a **custom exception hierarchy** so one bad record is logged and skipped, not fatal.
- Implement **rule-based logic** (the allocation algorithm) that reads its rules from configuration rather than hard-coding them.

## 🛠️ Your Mission

Complete `starter.py` so that, when run, it:

1. Loads the posting rules from `posting_config.json`.
2. Reads every corps-member record from `corps_members.txt`.
3. Parses and validates each record with regex.
4. Allocates each valid member to a posting state (never their origin; only states with free slots).
5. Writes a `posting_report.txt`, a `postings.json`, and appends to an `audit.log`.
6. Rejects and logs every bad record with a clear reason — and always prints a final run summary.

## 🏗️ Project Architecture

The program is a **pipeline** — data flows through these stages:

```
posting_config.json ─┐
                     ├─► LOAD ─► READ ─► SPLIT ─► PARSE ─► VALIDATE ─► ALLOCATE ─► OUTPUT ─► SUMMARY
corps_members.txt ───┘                                    (regex)     (rules)     (files)   (finally)
```

Each stage is one (or a few) functions in the starter. Build them one at a time and re-run often.

## 📋 Requirements

1. **`load_config(path)`** — return the JSON config as a dict (use `json.load`).
2. **`read_records(path)`** — return the whole records file as one string.
3. **`split_records(text)`** — split the text into individual member blocks on the `=== CORPS MEMBER ===` marker.
4. **`parse_record(block)`** — use regex to extract `name`, `callup`, `origin`, `course`, `phone`. It must:
   - Raise **`MalformedRecordError`** if any required field is missing.
   - Validate the **call-up number** format (`NYSC/YYYY/L/NNNN`, e.g. `NYSC/2026/A/0451`); reject anything else.
   - Validate the **phone number** (a valid Nigerian mobile number — `0` + 10 digits); reject `12345`.
   - Validate **origin** against the list of real Nigerian states (provided in the starter); reject `Wakanda`.
5. **`allocate_state(member, remaining_slots, config)`** — pick a state that is **not** the member's origin and still has a free slot; **decrement** that state's remaining slots; raise **`NoAvailableStateError`** if nothing fits.
6. **`write_posting_report(path, postings)`** — a readable report of who was posted where.
7. **`write_postings_json(path, postings)`** — the same data as JSON (`json.dump`).
8. **`append_audit(log_path, message)`** — **append** one line per event (open in `"a"` mode, never overwrite).
9. **`process_members(config, raw_text)`** — orchestrate with `try` / `except PostingError` / `finally`; keep counters; track remaining slots across the whole batch; print a run summary in `finally`.
10. The program must **never crash**, no matter how broken a record is.

## 📂 Provided Files

- **`corps_members.txt`** — ~11 records separated by `=== CORPS MEMBER ===`. Includes five deliberately broken records: one missing `ORIGIN`, one with a malformed call-up (`2026-IB-99`), one with a bad phone (`12345`), one whose origin is `Wakanda`, and one blank record.
- **`posting_config.json`** — the rules: `available_states`, `slots_per_state`, `priority_disciplines`, `min_capacity_warning`.
- **`starter.py`** — your working file: stubs grouped by pipeline stage, each with a docstring and `# TODO:`. It runs as-is (stubs return placeholders), and it already includes the `NIGERIAN_STATES` list for you.

## 🧭 Step-by-Step Guide

1. **Warm up:** run `python starter.py`. It should print a placeholder line without crashing.
2. **Stage 1 — Load & Read:** finish `load_config` and `read_records`. Print the config to confirm it loaded.
3. **Stage 2 — Split:** finish `split_records`. Print how many blocks you got.
4. **Stage 2 — Parse:** finish `parse_record` for a **valid** record first (get the five fields out). Only then add the four validations (missing field, call-up, phone, origin), each raising `MalformedRecordError`.
5. **Stage 3 — Allocate:** finish `allocate_state`. Test it by hand: a Lagos member should never get Lagos.
6. **Stage 4 — Output:** finish `write_posting_report`, `write_postings_json`, and `append_audit`.
7. **Stage 5 — Orchestrate:** finish `process_members` — the `try`/`except PostingError`/`finally` loop that ties it all together and tracks `remaining_slots`. Wire it up in `main`.
8. Compare your output to the Example Run below.

## 🖥️ Example Run

```
=== NYSC Posting Allocation Engine ===
Loaded 8 available states. Reading records...

POSTED  ✅  Chinedu Okafor (Anambra, Medicine)      -> Lagos
POSTED  ✅  Aisha Bello (Kano, Education)            -> Lagos
POSTED  ✅  Emeka Nwosu (Enugu, Agriculture)         -> Kano
REJECTED 🛑  <blank record> : missing required field 'name'
REJECTED 🛑  Ngozi Eze : missing required field 'origin'
REJECTED 🛑  Ibrahim Musa : invalid call-up number '2026-IB-99'
REJECTED 🛑  Blessing Johnson : invalid phone number '12345'
REJECTED 🛑  Yakubu Danjuma : 'Wakanda' is not a Nigerian state
POSTED  ✅  Chioma Okeke (Imo, Education)            -> Kano
...

============================================================
📊 RUN SUMMARY
   Records read : 11
   Posted       : 6
   Rejected     : 5
   Remaining slots: Lagos 0 | Kano 1 | Rivers 2 | Enugu 2 | Sokoto 3 | ...

Wrote: posting_report.txt, postings.json  (and appended to audit.log)
```

> Your exact wording, ordering, and which state each member lands in can differ — what matters is: origins are never reused, full states are skipped, every bad record is rejected with a reason, and the summary always prints.

## 🌟 Stretch Goals

1. **Priority disciplines:** when a member's course is in `priority_disciplines` (Medicine, Education, Agriculture), prefer posting them to a state that still has the most room, so critical skills get spread out.
2. **Capacity warning:** after allocation, use `min_capacity_warning` to print a ⚠️ for any state left with too few slots.
3. **Redeployment appeals:** add a second pass that tries to re-post any member who was rejected with `NoAvailableStateError` once other rejections have freed nothing — or report clearly that the batch is over capacity.
4. **Call-up letters:** for each posted member, write an individual `letters/<callup>.txt` mock call-up letter (great File Handling practice).

## 💡 Hints

- 🧩 To capture a field like the course, a pattern shaped like `r"COURSE:\s*(.+)"` finds the line and `match.group(1).strip()` gives the value — design your own for each field.
- 🔒 Validate a whole field's format with `re.fullmatch(...)` (it must match end-to-end), not just `re.search(...)` (which matches anywhere).
- 🧯 Because `MalformedRecordError` and `NoAvailableStateError` both inherit from `PostingError`, a single `except PostingError as err:` catches both — and `str(err)` gives you the reason to log.
- 🧮 Copy the slot counts once with `remaining_slots = dict(config["slots_per_state"])` so you can decrement freely without touching the config.
- 🎯 Keep the allocation simple: loop the available states in order, `continue` past the origin and any full state, take the first that fits, subtract one, return it. Only raise `NoAvailableStateError` if the loop finds nothing.
- 📎 A blank record split from the file will have no `NAME:` line — that's exactly the "missing required field" case, so it should fall through your normal validation, not need special handling.

## ✅ Submission Checklist

- [ ] `python starter.py` runs from start to finish **without ever crashing**
- [ ] `load_config` and `read_records` correctly read the JSON and the text file
- [ ] `parse_record` extracts all five fields with regex
- [ ] Missing fields, bad call-up numbers, bad phones, and non-Nigerian origins are all rejected via `MalformedRecordError`
- [ ] `allocate_state` never posts anyone to their origin and never exceeds a state's slots
- [ ] `NoAvailableStateError` is raised (and handled) when no state fits
- [ ] `posting_report.txt` and `postings.json` are written; `audit.log` is **appended** to (not overwritten)
- [ ] `process_members` uses `try` / `except PostingError` / `finally` and prints a full run summary
- [ ] All five broken records are rejected with clear reasons, and every valid member is posted 🎉
