"""The report's "What was sent to the model" section, from the scores of runs with --capture-prompt or --thinking (Phase 5 Task 31).

One line per eval file and target says how the text of its `source` was
looked for, in words that fit its kind (decision 6), then one line per
variant says, run by run, where it was found: in the run's first model call
(and in which field and under which line), which is what the harness sent
as instructions, or only in a later request, which also holds what the
agent read, or not at all; and says so plainly when part of what was sent
wasn't searched. One more line per file and target gives the thinking level
each run asked for and the level that reached the model. Logs whose runs
recorded neither add no section, so a report of a run without either flag
is as it was.
"""

from collections import Counter, defaultdict

from whitney.capture import (
    FIRST,
    LATER,
    LEVEL_CUT,
    LEVEL_NOT_RECORDED,
    NO_MODEL_REQUEST,
    NO_THINKING_SETTING,
    NOT_CAPTURED,
    NOT_SET,
    source_kind,
)

HEADING = "What was sent to the model"
EXACT = "exact text after normalising line endings and trailing spaces"
EXACT_CLAUDE_CODE = "exact text after removing block-level HTML comments, as Claude Code does, and normalising line endings and trailing spaces"
FIRST_COUNTS = "Only what the first model call held counts as sent as an instruction."
LATER_ONLY = "only in a later request (for example a file the agent read)"
NOT_SENT_AS_TEXT = "not sent as text: enforced by the harness"
ROLE_ORDER = {"baseline": 0, "template": 1, "control": 2}
PLAIN = frozenset({NOT_SET, NOT_CAPTURED, LEVEL_NOT_RECORDED, LEVEL_CUT, NO_MODEL_REQUEST, NO_THINKING_SETTING})  # Whitney's own words
THINKING_LABELS = {"set": "asked for", "asked": "asked for", "sent": "reached the model", "applied": "reached the model", "text": "thinking text"}


def render_capture_markdown(logs: list) -> str:
    """The section for `logs`, or "" if no run in them recorded what was sent or its thinking.

    Only logs that succeeded are read, and only samples with a score; a run
    whose score has no `sent` record, or one whose `found` entries aren't
    records, is counted as not captured.
    """
    files: dict[tuple[str, str], dict] = {}
    for log in logs:
        metadata = log.eval.metadata or {}
        if log.status != "success" or not metadata.get("eval_file"):
            continue
        key = (str(metadata["eval_file"]), str(metadata.get("target", "kilo")))
        entry = files.setdefault(key, {"source": metadata.get("source"), "variants": {}, "thinking": []})
        role = ROLE_ORDER.get(str(metadata.get("variant_role")), len(ROLE_ORDER))
        variant = entry["variants"].setdefault(str(metadata.get("variant")), {"rank": role, "sent": [], "not_captured": 0})
        for score in _scores(log):
            sent, thinking = score.get("sent"), score.get("thinking")
            if _is_capture(sent):
                variant["sent"].append(sent)
            else:
                variant["not_captured"] += 1
            if isinstance(thinking, dict):
                entry["thinking"].append(thinking)
    if not any(v["sent"] for entry in files.values() for v in entry["variants"].values()) and not any(
        entry["thinking"] for entry in files.values()
    ):
        return ""
    lines = [f"## {HEADING}", ""]
    for (eval_file, target), entry in sorted(files.items()):
        lines += _file_lines(f"- `{eval_file}` on {target}: ", entry, target)
        if entry["thinking"]:
            lines.append(f"- `{eval_file}` on {target}, thinking: {_thinking_line(entry['thinking'])}.")
    return "\n".join(lines) + "\n"


def _is_capture(sent) -> bool:
    """True for a score's `sent` record whose `found` maps each label to a record, as Capture writes it."""
    return isinstance(sent, dict) and isinstance(sent.get("found"), dict) and all(isinstance(v, dict) for v in sent["found"].values())


def _scores(log) -> list[dict]:
    """The metadata of each scored sample's first score, skipping samples that errored or have none."""
    out = []
    for sample in log.samples or []:
        if sample.error or not sample.scores:
            continue
        metadata = getattr(next(iter(sample.scores.values())), "metadata", None)
        out.append(metadata if isinstance(metadata, dict) else {})
    return out


def _file_lines(start: str, entry: dict, target: str) -> list[str]:
    """The file's line, saying how its source text was looked for, then one line per variant saying where it was found."""
    source = entry["source"]
    kind = source_kind(source)
    if kind is None:
        return [f"{start}no `source` to look for."]
    if kind == "config":
        return [f"{start}`{source}`: {NOT_SENT_AS_TEXT}."]
    variants = sorted(entry["variants"].items(), key=lambda item: (item[1]["rank"], item[0]))
    if not any(v["sent"] for _, v in variants):
        return [f"{start}{NOT_CAPTURED}."]
    exact = EXACT_CLAUDE_CODE if target == "claude-code" and kind == "instructions" else EXACT
    if kind in ("skill", "agent"):
        how = f"{kind} `{source}`, its description and its body looked for separately"
    elif target == "claude-code" and source == "AGENTS.md":
        how = f"`AGENTS.md` stands for the instruction files staged and the files they import, each looked for as its {exact}"
    else:
        how = f"instruction file `{source}`, looked for as its {exact}"
    lines = [f"{start}{how}. {FIRST_COUNTS}"]
    return lines + [f"  - {name}: {_variant_counts(v, quoted=kind == 'instructions')}." for name, v in variants]


def _variant_counts(variant: dict, *, quoted: bool) -> str:
    """One variant's counts: where each label was found in its captured runs, then what wasn't captured or searched."""
    runs = variant["sent"]
    if not runs:
        return NOT_CAPTURED
    labels = list(dict.fromkeys(label for sent in runs for label in sent["found"]))
    counts = [_label_counts(f"`{label}`" if quoted else label, [sent["found"][label] for sent in runs if label in sent["found"]]) for label in labels]
    text = "; ".join(counts) or "nothing to look for"
    if variant["not_captured"]:
        text += f" ({_runs(variant['not_captured'])} not captured)"
    unlogged = sum(sent.get("unlogged") or 0 for sent in runs)
    if unlogged:
        text += f"; Inspect logged no call for {unlogged} model events"
    others = sum(sent.get("not_model_calls") or 0 for sent in runs)
    if others:
        text += f"; {others} other requests weren't the run's model call and weren't searched" if others > 1 else (
            "; 1 other request wasn't the run's model call and wasn't searched"
        )
    return text


def _label_counts(name: str, places: list[dict]) -> str:
    """Where one label was found, run by run: in the first model call, not in it (and if only later), or not searched."""
    total = len(places)
    first = [place for place in places if place.get("in") == FIRST]
    unsearched = Counter(place["not_searched"] for place in places if place.get("not_searched"))  # never on a FIRST place
    not_first = [place for place in places if place.get("in") != FIRST and not place.get("not_searched")]
    clauses = []
    if first:
        clauses.append(f"in the first model call in {len(first)} of {total} runs ({_places(first)})")
    if not_first:
        later = sum(place.get("in") == LATER for place in not_first)
        clauses.append(f"not in the first model call in {len(not_first)} of {total} runs" + (f", {LATER_ONLY} in {later}" if later else ""))
    clauses += [f"not searched in {count} of {total} runs ({why})" for why, count in unsearched.items()]
    return f"{name} " + ", ".join(clauses)


def _places(first: list[dict]) -> str:
    """Each field and heading the label was found under in the first model call, with a count when they differ."""
    counted = Counter(_place(place) for place in first)
    if len(counted) == 1:
        return next(iter(counted))
    return "; ".join(f"{place} in {count}" for place, count in counted.items())


def _place(place: dict) -> str:
    field = place.get("field") or "in what was read before the cut"
    return f"{field}, under `{place['heading']}`" if place.get("heading") else field


def _thinking_line(records: list[dict]) -> str:
    """Each field of the runs' thinking records, under the report's name for it, with each value it took and in how many runs."""
    values: dict[str, Counter] = defaultdict(Counter)
    for record in records:
        for field, value in record.items():
            values[THINKING_LABELS.get(field, field)][str(value)] += 1
    return "; ".join(
        f"{label}: " + ", ".join(f"{_shown(label, value)} ({_runs(count)})" for value, count in counted.items())
        for label, counted in values.items()
    )


def _shown(label: str, value: str) -> str:
    """A thinking value as the report shows it: a setting quoted as code, Whitney's own words as they are."""
    return value if label == THINKING_LABELS["text"] or value in PLAIN else f"`{value}`"


def _runs(count: int) -> str:
    return f"{count} run" if count == 1 else f"{count} runs"
