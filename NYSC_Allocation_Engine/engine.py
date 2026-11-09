"""
NYSC Posting Allocation Engine  ---  COMPLETE, HARDENED VERSION

Posts fresh graduates ("corps members") to states for their NYSC service year,
honouring the golden rule: NEVER post someone to their own state of origin.

PIPELINE
  LOAD -> READ -> SPLIT -> PARSE -> VALIDATE -> ALLOCATE -> (APPEAL) -> OUTPUT -> SUMMARY

DESIGN PRINCIPLES (why it never crashes)
  1. Every stage that touches the outside world (files, JSON, console) fails
     soft: it reports the problem and carries on with a safe fallback.
  2. Every record is processed inside its own try/except, so one bad record
     (or even an unexpected bug) can never stop the batch.
  3. The run summary lives in a `finally` block, so it ALWAYS prints.
  4. The rules (states, slots, priority courses, thresholds) come from
     posting_config.json, and a damaged or missing config falls back to
     sensible built-in defaults.

Beyond the brief, this version also includes:
  * duplicate call-up detection        * tolerant state names ("akwa-ibom", "Abuja")
  * Nigerian phone normalisation       * atomic file writes (no half-written files)
  * capacity warnings                  * redeployment appeals (swap-based second pass)
  * individual call-up letters         * timestamped, run-tagged audit trail
"""

import re
import json
import os
import sys
import textwrap
import traceback
from datetime import datetime

# ---------------------------------------------------------------------------
# SCRIPT-RELATIVE PATHS (work no matter where the script is launched from)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH   = os.path.join(BASE_DIR, "posting_config.json")
RECORDS_PATH  = os.path.join(BASE_DIR, "corps_members.txt")
POSTING_TXT   = os.path.join(BASE_DIR, "posting_report.txt")
POSTINGS_JSON = os.path.join(BASE_DIR, "postings.json")
AUDIT_LOG     = os.path.join(BASE_DIR, "audit.log")
LETTERS_DIR   = os.path.join(BASE_DIR, "letters")

RECORD_MARKER = "=== CORPS MEMBER ==="
# Slightly forgiving version of the marker (case, spacing) used for splitting.
RECORD_PATTERN = re.compile(r"={3,}[ \t]*CORPS[ \t]+MEMBER[ \t]*={3,}", re.IGNORECASE)

REQUIRED_FIELDS = ["name", "callup", "origin", "course", "phone"]

# 36 states + FCT. Used to validate ORIGIN (an FCT indigene is a valid origin).
NIGERIAN_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue",
    "Borno", "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu",
    "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi",
    "Kwara", "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo",
    "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe", "Zamfara", "FCT",
]

DEFAULT_PRIORITY_COURSES = [
    "Medicine", "Nursing", "Pharmacy", "Medical Laboratory Science",
    "Education", "Agriculture", "Engineering", "Computer Science",
]


# ===========================================================================
# SMALL SAFE HELPERS
# ===========================================================================

def say(text=""):
    """print() that can never raise (odd consoles, closed pipes, emoji on old Windows)."""
    try:
        print(text)
    except (UnicodeEncodeError, OSError, ValueError):
        try:
            print(str(text).encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _state_key(text):
    """'Akwa-Ibom ', 'AKWA IBOM', 'akwa ibom' -> 'akwaibom' (letters only, lowercase)."""
    if text is None:
        return ""
    return re.sub(r"[^a-z]", "", str(text).lower())


# Other ways people write a state's name. The official spellings come straight
# from NIGERIAN_STATES, so only the extras live here.
_STATE_ALIASES = {
    "abuja": "FCT", "fctabuja": "FCT", "federalcapitalterritory": "FCT",
    "nassarawa": "Nasarawa",            # very common misspelling
}

# Typo tolerance for state names. Humans misspell; rejecting "Anambara" would
# be wrong. But a WRONG correction could post someone to their own origin, so
# we only auto-correct when exactly one state is clearly the closest.
FUZZY_STATE_MATCHING = True


def _edit_distance(a, b):
    """
    Number of single-letter edits (insert, delete, replace, or swap two
    neighbouring letters) needed to turn `a` into `b`.
    """
    previous2, previous = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                current[j] = min(current[j], previous2[j - 2] + 1)      # swapped neighbours
        previous2, previous = previous, current
    return previous[len(b)]


def resolve_state(text):
    """
    Work out which Nigerian state a (possibly misspelt) name refers to.

    Returns (state, status, options):
      ("Anambra", "exact",     [])        spelt correctly (or a known alias)
      ("Anambra", "corrected", [])        misspelt, but ONE state is clearly closest
      (None,      "ambiguous", ["Imo", "Edo"])   too close to call between several states
      (None,      "unknown",   [])        not a state at all ("Wakanda", "Nigeria", ...)
    """
    key = _state_key(text)
    if not key:
        return None, "unknown", []

    # Every spelling we accept, paired with the official state it means.
    candidates = [(_state_key(state), state) for state in NIGERIAN_STATES] + list(_STATE_ALIASES.items())

    # 1) Exact match FIRST. This must come before any distance maths: "Osun" is
    #    one letter from "Ogun", so without this shortcut a perfectly spelt
    #    "Osun" would look ambiguous and be rejected.
    for spelling, state in candidates:
        if key == spelling:
            return state, "exact", []

    # 2) Otherwise, only try to repair typos if that feature is on and the word is long enough.
    if not FUZZY_STATE_MATCHING or len(key) < 3:
        return None, "unknown", []

    best = {}                                         # state -> smallest distance over its spellings
    for spelling, state in candidates:
        distance = _edit_distance(key, spelling)
        if distance < best.get(state, 99):
            best[state] = distance
    ranked = sorted(best.items(), key=lambda item: (item[1], item[0]))

    allowed = 1 if len(key) <= 7 else 2               # longer names tolerate more typos
    margin = 2 if len(key) <= 4 else 1                # short names must win by more (Imo/Edo/Oyo...)
    top_state, top_distance = ranked[0]
    if top_distance > allowed:
        return None, "unknown", []
    runner_up = ranked[1][1]
    if runner_up - top_distance >= margin:
        return top_state, "corrected", []
    options = [state for state, distance in ranked if distance <= max(top_distance + margin - 1, allowed)][:3]
    return None, "ambiguous", options


def canonical_state(text):
    """Return the official spelling of a Nigerian state (typos tolerated), or None."""
    return resolve_state(text)[0]


def _to_nonneg_int(value):
    """Best-effort conversion to an int >= 0; returns None if impossible."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(number, 0)


# ===========================================================================
# STAGE 0 : CUSTOM EXCEPTION HIERARCHY
# ===========================================================================

class PostingError(Exception):
    """Base class for every error our posting engine knows how to handle."""


class MalformedRecordError(PostingError):
    """A record is missing required fields or contains bad data."""


class DuplicateRecordError(MalformedRecordError):
    """The same call-up number appeared more than once in the batch."""


class NoAvailableStateError(PostingError):
    """No eligible posting state has a free slot for a member."""


# ===========================================================================
# STAGE 1 : LOAD  (File Handling + JSON)
# ===========================================================================

def default_config():
    """Built-in fallback rules, used if posting_config.json is missing or broken."""
    states = [s for s in NIGERIAN_STATES if s != "FCT"]          # the 36 states
    return {
        "states_of_Nigeria": states,
        "slots_per_state": {s: 100 for s in states},
        "course_of_study": list(DEFAULT_PRIORITY_COURSES),
        "min_capacity_warning": 10,
        "regular_strategy": "first_fit",
        "generate_call_up_letters": True,
    }


def normalize_config(raw, warn=None):
    """
    Turn whatever json.load gave us into a clean, trustworthy config dict.
    Bad or missing pieces are repaired (and reported through `warn`) rather
    than crashing the run later.
    """
    warn = warn or (lambda message: None)
    cfg = default_config()

    if not isinstance(raw, dict):
        warn("config is not a JSON object - using built-in defaults")
        return cfg

    # --- states -----------------------------------------------------------
    raw_states = raw.get("states_of_Nigeria", raw.get("available_states"))
    states = []
    if isinstance(raw_states, list):
        for item in raw_states:
            canon = canonical_state(item) if isinstance(item, str) else None
            if canon is None:
                warn(f"ignoring unknown state in config: {item!r}")
            elif canon not in states:
                states.append(canon)
    if states:
        cfg["states_of_Nigeria"] = states
    else:
        warn("no usable 'states_of_Nigeria' in config - using the 36 default states")
    states = cfg["states_of_Nigeria"]

    # --- slots ------------------------------------------------------------
    raw_slots = raw.get("slots_per_state")
    if not isinstance(raw_slots, dict) or not raw_slots:
        warn("no usable 'slots_per_state' in config - using 100 slots per state")
        cfg["slots_per_state"] = {s: 100 for s in states}
    else:
        by_state = {}
        for key, value in raw_slots.items():
            canon = canonical_state(key)
            if canon:
                by_state[canon] = value
        slots = {}
        for state in states:
            count = _to_nonneg_int(by_state.get(state))
            if count is None:
                warn(f"bad or missing slot count for {state} - treating as 0")
                count = 0
            slots[state] = count
        cfg["slots_per_state"] = slots

    # --- priority courses -------------------------------------------------
    courses = raw.get("course_of_study", raw.get("priority_disciplines"))
    if isinstance(courses, dict):
        courses = list(courses.keys())
    if isinstance(courses, list):
        cleaned = [str(c).strip() for c in courses if str(c).strip()]
        if cleaned:
            cfg["course_of_study"] = cleaned

    # --- misc -------------------------------------------------------------
    threshold = _to_nonneg_int(raw.get("min_capacity_warning"))
    if threshold is not None:
        cfg["min_capacity_warning"] = threshold

    strategy = str(raw.get("regular_strategy", "first_fit")).strip().lower()
    cfg["regular_strategy"] = strategy if strategy in ("first_fit", "balanced") else "first_fit"

    letters = raw.get("generate_call_up_letters", True)
    cfg["generate_call_up_letters"] = letters if isinstance(letters, bool) else True
    return cfg


def load_config(path):
    """
    Read the JSON rules file and return a clean config dict.
    A missing, unreadable or corrupt file never crashes the run: we warn and
    fall back to the built-in defaults instead.
    """
    warnings = []
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raw = None
        warnings.append(f"config file not found ({os.path.basename(path)}) - using built-in defaults")
    except (OSError, ValueError) as err:       # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raw = None
        warnings.append(f"could not read config ({err.__class__.__name__}: {err}) - using built-in defaults")

    config = normalize_config(raw, warn=warnings.append) if raw is not None else default_config()
    for message in warnings:
        say(f"⚠️  CONFIG: {message}")
    return config


def read_records(path):
    """
    Return the whole records file as ONE string. If the file is missing or
    unreadable, say so and return an empty string (the run then reports
    'Records read: 0' instead of crashing).
    """
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            return handle.read()
    except OSError as err:
        say(f"⚠️  Could not read records file '{os.path.basename(str(path))}': {err}")
        return ""


# ===========================================================================
# STAGE 2 : SPLIT + PARSE  (Regular Expressions)
# ===========================================================================

def split_records(text):
    """
    Split the raw text into member blocks on the '=== CORPS MEMBER ===' marker.

    Blank blocks are KEPT on purpose - they must reach parse_record and be
    rejected there. Only the empty chunk before the very first marker is
    dropped (it is not a record, just the start of the file).
    """
    if not isinstance(text, str) or not text.strip():
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = RECORD_PATTERN.split(text)
    if chunks and not chunks[0].strip():
        chunks = chunks[1:]
    return chunks


# One regex per field: "LABEL: value" on its own line, any case, any spacing.
FIELD_PATTERNS = {
    "name":   re.compile(r"^[ \t]*NAME[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE),
    "callup": re.compile(r"^[ \t]*CALL[ _-]?UP[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE),
    "origin": re.compile(r"^[ \t]*ORIGIN[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE),
    "course": re.compile(r"^[ \t]*COURSE[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE),
    "phone":  re.compile(r"^[ \t]*PHONE[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE),
}

CALLUP_RE = re.compile(r"NYSC/\d{4}/[A-Z]/\d{4}")                 # NYSC/2026/A/0451
PHONE_RE  = re.compile(r"0[789][01]\d{8}")                        # 0 + 10 digits, real mobile prefixes
NAME_RE   = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .'’-])*")          # letters, spaces, . ' -


def _extract(block, field):
    """Return the cleaned value of one field, or None if absent/empty."""
    match = FIELD_PATTERNS[field].search(block)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip()
    return value or None


def peek_name(block):
    """Best-effort label for a record, used only for friendly reject messages."""
    try:
        if not isinstance(block, str) or not block.strip():
            return "<blank record>"
        name = _extract(block.replace("\r", "\n"), "name") or "<unnamed record>"
        return name if len(name) <= 40 else name[:37] + "..."
    except Exception:
        return "<unreadable record>"


def normalize_phone(raw_phone):
    """'+234 803 123 4567' / '234803...' / '0803-123-4567' -> '08031234567'."""
    digits = re.sub(r"[ \t().-]", "", raw_phone)
    if digits.startswith("+234"):
        digits = "0" + digits[4:]
    elif digits.startswith("234") and len(digits) == 13:
        digits = "0" + digits[3:]
    return digits


def parse_record(block):
    """
    Turn ONE raw member block into a clean, validated dictionary.
    Raises MalformedRecordError (with a clear reason) on any problem.
    """
    if not isinstance(block, str):
        raise MalformedRecordError("record is not text")
    block = block.replace("\r\n", "\n").replace("\r", "\n")

    raw = {field: _extract(block, field) for field in REQUIRED_FIELDS}

    missing = [field for field in REQUIRED_FIELDS if raw[field] is None]
    if len(missing) == 1:
        raise MalformedRecordError(f"missing required field '{missing[0]}'")
    if missing:
        raise MalformedRecordError("missing required fields: " + ", ".join(f"'{m}'" for m in missing))

    name = raw["name"]
    if len(name) < 2 or len(name) > 80 or not NAME_RE.fullmatch(name):
        raise MalformedRecordError(f"invalid name '{name[:40]}'")

    callup = raw["callup"].upper()
    if not CALLUP_RE.fullmatch(callup):
        raise MalformedRecordError(f"invalid call-up number '{raw['callup'][:40]}'")

    origin, how, options = resolve_state(raw["origin"])
    if origin is None:
        hint = f" (did you mean {' or '.join(options)}?)" if options else ""
        raise MalformedRecordError(f"'{raw['origin'][:40]}' is not a Nigerian state{hint}")

    course = raw["course"]
    if len(course) < 2 or len(course) > 80 or re.search(r"\d", course):
        raise MalformedRecordError(f"invalid course '{course[:40]}'")

    phone = normalize_phone(raw["phone"])
    if not PHONE_RE.fullmatch(phone):
        raise MalformedRecordError(f"invalid phone number '{raw['phone'][:40]}'")

    member = {"name": name, "callup": callup, "origin": origin, "course": course, "phone": phone}
    if how == "corrected":
        member["origin_as_written"] = raw["origin"]          # keep an audit trail of the auto-correction
    return member


# ===========================================================================
# STAGE 3 : ALLOCATE  (rule-based, config-driven)
# ===========================================================================

def _priority_courses(config):
    courses = config.get("course_of_study") or config.get("priority_disciplines") or []
    if isinstance(courses, dict):
        courses = courses.keys()
    return {str(c).strip().lower() for c in courses}


def allocate_state(member, remaining_slots, config):
    """
    Choose a posting state for one validated member.

    RULES
      * never the member's own state of origin
      * only states with a free slot
      * priority courses (config 'course_of_study') go to the state with the
        MOST room left, spreading scarce skills across the federation
      * everyone else: 'first_fit' (first eligible state in config order) or
        'balanced' (most room left) depending on config 'regular_strategy'
      * the chosen state's slot count is decremented
    Raises NoAvailableStateError if nothing fits.
    """
    states = config.get("states_of_Nigeria") or config.get("available_states") or list(remaining_slots)
    origin_key = _state_key(member.get("origin"))

    eligible = [
        s for s in states
        if _state_key(s) != origin_key and (_to_nonneg_int(remaining_slots.get(s)) or 0) > 0
    ]
    if not eligible:
        raise NoAvailableStateError(
            f"no state with a free slot is available for {member.get('name', 'member')} "
            f"(origin: {member.get('origin')})"
        )

    is_priority = str(member.get("course", "")).strip().lower() in _priority_courses(config)
    if is_priority or config.get("regular_strategy") == "balanced":
        chosen = max(eligible, key=lambda s: remaining_slots[s])    # ties -> config order
    else:
        chosen = eligible[0]

    remaining_slots[chosen] -= 1
    return chosen


def try_redeploy(member, postings, remaining_slots, config):
    """
    APPEAL pass for a member who got NoAvailableStateError.

    Slots never come back, so a plain retry is pointless. But a SWAP can work:
    the only free slots left are in states the member can't use (their origin).
    If an already-posted member from elsewhere can move into such a free slot,
    the appellant takes the state that member vacates. Returns the new state
    name, or None if no swap exists.
    """
    states = config.get("states_of_Nigeria") or list(remaining_slots)
    origin_key = _state_key(member.get("origin"))
    free_states = [s for s in states if (_to_nonneg_int(remaining_slots.get(s)) or 0) > 0]

    for posted in postings:
        vacated = posted["state"]
        if _state_key(vacated) == origin_key:
            continue                                   # APPELLANT can't take THEIR own origin
        for free in free_states:
            if _state_key(free) == _state_key(posted["origin"]):
                continue                               # the MOVED MEMBER can't go to THEIR origin
            if free == vacated:
                continue
            remaining_slots[free] -= 1                 # moved member takes the free slot
            posted["redeployed_from"] = vacated
            posted["state"] = free
            return vacated                             # appellant takes the vacated slot
    return None


# ===========================================================================
# STAGE 4 : OUTPUT  (text report, JSON, append-only audit log, letters)
# ===========================================================================

def _atomic_write(path, write_fn):
    """Write to a temp file then swap it in, so a crash never leaves a half-written file."""
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            write_fn(handle)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def write_posting_report(path, postings, rejections=None):
    """Write the human-readable posting report (posted table + not-posted list)."""
    bar = "=" * 86

    def render(handle):
        handle.write(f"{bar}\nNATIONAL YOUTH SERVICE CORPS - POSTING REPORT\n"
                     f"Generated: {_now()}\n{bar}\n")
        handle.write(f"Corps members posted: {len(postings)}\n\n")
        handle.write(f"{'#':>3}  {'Name':<24}{'Call-up No.':<18}{'Origin':<13}{'Course':<19}Posted to\n")
        handle.write("-" * 86 + "\n")
        for number, p in enumerate(postings, start=1):
            handle.write(
                f"{number:>3}  {p['name'][:23]:<24}{p['callup']:<18}{p['origin']:<13}"
                f"{p['course'][:18]:<19}{p['state']}\n"
            )
        if rejections:
            handle.write(f"\n{bar}\nNOT POSTED ({len(rejections)})\n{bar}\n")
            for r in rejections:
                handle.write(f"- {r['record']}: {r['reason']}\n")

    _atomic_write(path, render)


def write_postings_json(path, postings):
    """Write the machine-readable postings list as JSON."""
    _atomic_write(path, lambda handle: json.dump(postings, handle, indent=2, ensure_ascii=False))


_AUDIT_STATE = {"warned": False}


def append_audit(log_path, message, run_id=None):
    """
    APPEND one timestamped line to the audit trail (mode "a" - never overwritten).
    Returns True on success; on failure it warns and returns False (never raises).
    """
    line = " ".join(str(message).split())               # guarantee exactly one line
    prefix = f"{_now()} | {run_id} | " if run_id else f"{_now()} | "
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(prefix + line + "\n")
        return True
    except OSError as err:
        if not _AUDIT_STATE["warned"]:                  # warn once, not on every line
            _AUDIT_STATE["warned"] = True
            say(f"⚠️  Could not write audit log (further audit warnings suppressed): {err}")
        return False


def write_call_up_letters(directory, postings):
    """Stretch goal: one mock call-up letter per posted member. Returns how many were written."""
    written = 0
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as err:
        say(f"⚠️  Could not create letters folder: {err}")
        return 0
    for p in postings:
        file_name = re.sub(r"[^A-Za-z0-9_-]", "_", p["callup"]) + ".txt"      # '/' is not allowed in file names
        body = (
            "NATIONAL YOUTH SERVICE CORPS\n"
            "CALL-UP LETTER (MOCK)\n"
            f"{'=' * 40}\n"
            f"Call-up No.: {p['callup']}\n"
            f"Date issued: {datetime.now():%d %B %Y}\n\n"
            f"Dear {p['name']},\n\n"
            f"Following the posting exercise, you have been posted to {p['state'].upper()} STATE\n"
            f"for your one-year National Youth Service.\n\n"
            f"  State of origin : {p['origin']}\n"
            f"  Course of study : {p['course']}\n"
            f"  Posted to       : {p['state']}\n\n"
            "Please report to the NYSC Orientation Camp in your posting state.\n\n"
            "Signed,\nDirector-General (mock)\n"
        )
        try:
            with open(os.path.join(directory, file_name), "w", encoding="utf-8") as handle:
                handle.write(body)
            written += 1
        except OSError as err:
            say(f"⚠️  Could not write letter for {p['callup']}: {err}")
    return written


def _guard(label, function, *args, **kwargs):
    """Run an output step; if it fails, report it and carry on."""
    try:
        function(*args, **kwargs)
        return True
    except Exception as err:
        say(f"⚠️  Could not write {label}: {err.__class__.__name__}: {err}")
        return False


# ===========================================================================
# STAGE 5 : ORCHESTRATE  (try / except PostingError / finally)
# ===========================================================================

def print_summary(total, posted, rejected, remaining_slots, config):
    """The final run summary (also prints capacity warnings)."""
    states = config.get("states_of_Nigeria") or list(remaining_slots)
    say("\n" + "=" * 60)
    say("📊 RUN SUMMARY")
    say(f"   Records read : {total}")
    say(f"   Posted       : {posted}")
    say(f"   Rejected     : {rejected}")
    say(f"   Free slots left in total: {sum(max(remaining_slots.get(s, 0), 0) for s in states)}")
    # Non-breaking spaces keep "Akwa Ibom 3" together when the line wraps.
    items = [f"{s} {remaining_slots.get(s, 0)}".replace(" ", "\u00a0") for s in states]
    say("   Remaining slots:")
    for wrapped in textwrap.wrap(" | ".join(items), width=70):
        say("     " + wrapped.replace("\u00a0", " "))

    threshold = config.get("min_capacity_warning") or 0
    low = [s for s in states if remaining_slots.get(s, 0) < threshold]
    if low:
        say(f"   ⚠️  Low capacity (fewer than {threshold} slots left): " + ", ".join(low))


def process_members(config, raw_text):
    """
    Walk every record and survive every bad one.
    Returns {"postings": [...], "rejections": [...], "stats": {...}}.
    """
    run_id = datetime.now().strftime("RUN-%Y%m%d-%H%M%S")
    states = config.get("states_of_Nigeria") or list(config.get("slots_per_state", {}))
    remaining_slots = dict(config.get("slots_per_state", {}))        # copy: never mutate the config

    postings, rejections, pending_appeals = [], [], []
    seen_callups = set()
    total = posted = rejected = 0

    try:
        blocks = split_records(raw_text)
        append_audit(AUDIT_LOG, f"RUN START | records={len(blocks)} | states={len(states)}", run_id)
        say(f"Loaded {len(states)} available states. Reading records...\n")

        for block in blocks:
            total += 1
            label = peek_name(block)
            member = None
            try:
                member = parse_record(block)                         # may raise MalformedRecordError
                label = member["name"]
                if member["callup"] in seen_callups:
                    raise DuplicateRecordError(f"duplicate call-up number '{member['callup']}'")
                seen_callups.add(member["callup"])

                state = allocate_state(member, remaining_slots, config)   # may raise NoAvailableStateError
                member["state"] = state
                postings.append(member)
                posted += 1
                note = (f" [origin '{member['origin_as_written']}' read as '{member['origin']}']"
                        if "origin_as_written" in member else "")
                say(f"POSTED    ✅  {label} ({member['origin']}, {member['course']}) -> {state}{note}")
                append_audit(AUDIT_LOG, f"POSTED | {member['callup']} | {label} | "
                                        f"{member['origin']} -> {state}{note}", run_id)

            except PostingError as err:
                rejected += 1
                entry = {"record": label, "reason": str(err), "type": err.__class__.__name__}
                rejections.append(entry)
                if isinstance(err, NoAvailableStateError):
                    pending_appeals.append((member, entry))
                say(f"REJECTED  🛑  {label} : {err}")
                append_audit(AUDIT_LOG, f"REJECTED | {label} | {err}", run_id)

            except Exception as err:                                  # a genuine bug must not stop the batch
                rejected += 1
                reason = f"unexpected error ({err.__class__.__name__}: {err})"
                rejections.append({"record": label, "reason": reason, "type": err.__class__.__name__})
                say(f"REJECTED  🛑  {label} : {reason}")
                append_audit(AUDIT_LOG, f"REJECTED | {label} | {reason}", run_id)

        # ---- second pass: redeployment appeals ---------------------------
        if pending_appeals:
            say("\n🔁 Redeployment appeals (swap pass)...")
            for member, entry in pending_appeals:
                try:
                    new_state = try_redeploy(member, postings, remaining_slots, config)
                    if new_state is None:
                        entry["reason"] += " | appeal failed: batch is over capacity for this member"
                        say(f"   ❌ {member['name']}: no swap possible")
                        append_audit(AUDIT_LOG, f"APPEAL FAILED | {member['name']}", run_id)
                        continue
                    member["state"] = new_state
                    member["redeployed"] = True
                    postings.append(member)
                    rejections.remove(entry)
                    rejected -= 1
                    posted += 1
                    say(f"   ✅ {member['name']} re-posted -> {new_state}")
                    append_audit(AUDIT_LOG, f"REDEPLOYED | {member['callup']} | {member['name']} | "
                                            f"{member['origin']} -> {new_state}", run_id)
                except Exception as err:
                    say(f"   ⚠️ appeal for {member.get('name')} failed safely: {err}")
            still = [e for e in rejections if e["type"] == "NoAvailableStateError"]
            if still:
                free = sum(max(remaining_slots.get(s, 0), 0) for s in states)
                say(f"   ⚠️  {len(still)} member(s) could not be placed: the batch is over capacity "
                    f"({free} free slot(s) remain, but none usable by them).")

        # ---- outputs -----------------------------------------------------
        say("")
        ok_report = _guard("posting_report.txt", write_posting_report, POSTING_TXT, postings, rejections)
        ok_json = _guard("postings.json", write_postings_json, POSTINGS_JSON, postings)
        letters = 0
        if config.get("generate_call_up_letters", True):
            letters = write_call_up_letters(LETTERS_DIR, postings)
        wrote = [name for name, ok in (("posting_report.txt", ok_report), ("postings.json", ok_json)) if ok]
        say(f"Wrote: {', '.join(wrote) or 'nothing'}  (and appended to audit.log)"
            + (f"; {letters} call-up letter(s) in letters/" if letters else ""))

    finally:
        # Runs ALWAYS - even if something above blew up unexpectedly.
        append_audit(AUDIT_LOG, f"RUN END | read={total} posted={posted} rejected={rejected}", run_id)
        print_summary(total, posted, rejected, remaining_slots, config)

    return {
        "postings": postings,
        "rejections": rejections,
        "stats": {"read": total, "posted": posted, "rejected": rejected,
                  "remaining_slots": remaining_slots},
    }


def main():
    """Wire the whole pipeline together."""
    try:                                             # emoji-safe console on any platform
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    say("=== NYSC Posting Allocation Engine ===")
    try:
        config = load_config(CONFIG_PATH)
        raw_text = read_records(RECORDS_PATH)
        process_members(config, raw_text)
    except KeyboardInterrupt:
        say("\nRun interrupted by the user. Partial results may have been saved.")
    except Exception as err:                         # last line of defence
        say(f"\n⚠️  The engine hit an unexpected problem but shut down cleanly: "
            f"{err.__class__.__name__}: {err}")
        append_audit(AUDIT_LOG, "ENGINE FAULT | " + traceback.format_exc().splitlines()[-1])


if __name__ == "__main__":
    main()
