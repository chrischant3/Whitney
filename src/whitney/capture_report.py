"""The report's "What was sent to the model" section, from the scores of runs with --capture-prompt or --thinking (Phase 5 Task 31).

One line per eval file and target says whether the text of its `source`
was found in what was sent, in each variant, in words that fit its kind
(decision 6); one more line per file and target gives the thinking level
each run set and what reached the model. Logs whose runs recorded neither
add no section, so a report of a run without either flag is as it was.
"""

from collections import Counter, defaultdict

from whitney.capture import NO_MODEL_REQUEST, NO_THINKING_SETTING, NOT_CAPTURED, NOT_SET, source_kind

HEADING = "What was sent to the model"
EXACT = "exact text after normalising line endings and trailing spaces"
NOT_SENT_AS_TEXT = "not sent as text: enforced by the harness"
ROLE_ORDER = {"baseline": 0, "template": 1, "control": 2}
PLAIN = frozenset({NOT_SET, NOT_CAPTURED, NO_MODEL_REQUEST, NO_THINKING_SETTING})  # Whitney's own words, not quoted settings


def render_capture_markdown(logs: list) -> str:
    """The section for `logs`, or "" if no run in them recorded what was sent or its thinking.

    Only logs that succeeded are read, and only samples with a score; a run
    whose score has no `sent` record is counted as not captured.
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
            if isinstance(sent, dict) and isinstance(sent.get("found"), dict):
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
        lines.append(f"- `{eval_file}` on {target}: {_found_line(entry, target)}")
        if entry["thinking"]:
            lines.append(f"- `{eval_file}` on {target}, thinking: {_thinking_line(entry['thinking'])}.")
    return "\n".join(lines) + "\n"


def _scores(log) -> list[dict]:
    """The metadata of each scored sample's first score, skipping samples that errored or have none."""
    out = []
    for sample in log.samples or []:
        if sample.error or not sample.scores:
            continue
        metadata = getattr(next(iter(sample.scores.values())), "metadata", None)
        out.append(metadata if isinstance(metadata, dict) else {})
    return out


def _found_line(entry: dict, target: str) -> str:
    """Whether the file's source text was found, variant by variant, in the words its kind takes."""
    source = entry["source"]
    kind = source_kind(source)
    if kind is None:
        return "no `source` to look for."
    if kind == "config":
        return f"`{source}`: {NOT_SENT_AS_TEXT}."
    variants = sorted(entry["variants"].items(), key=lambda item: (item[1]["rank"], item[0]))
    if not any(v["sent"] for _, v in variants):
        return f"{NOT_CAPTURED}."
    if kind in ("skill", "agent"):
        how = f"{kind} `{source}`, its description and its body looked for separately"
    elif target == "claude-code" and source == "AGENTS.md":
        how = f"`AGENTS.md` stands for the instruction files staged and the files they import, each looked for as its {EXACT}"
    else:
        how = f"instruction file `{source}`, looked for as its {EXACT}"
    return f"{how}. " + "; ".join(f"{name}: {_variant_counts(v, quoted=kind == 'instructions')}" for name, v in variants) + "."


def _variant_counts(variant: dict, *, quoted: bool) -> str:
    """One variant's counts: each label, found in how many of its captured runs, and what wasn't captured."""
    runs = variant["sent"]
    if not runs:
        return NOT_CAPTURED
    labels = list(dict.fromkeys(label for sent in runs for label in sent["found"]))
    counts = []
    for label in labels:
        found = sum(sent["found"].get(label) is not None for sent in runs)
        name = f"`{label}`" if quoted else label
        counts.append(f"{name} found in {found} of {len(runs)} runs" if found else f"{name} not found (0 of {len(runs)} runs)")
    text = ", ".join(counts) or "nothing to look for"
    if variant["not_captured"]:
        text += f" ({_runs(variant['not_captured'])} not captured)"
    unlogged = sum(sent.get("unlogged") or 0 for sent in runs)
    if unlogged:
        text += f"; Inspect logged no call for {unlogged} model events"
    return text


def _thinking_line(records: list[dict]) -> str:
    """Each field of the runs' thinking records with each value it took and in how many runs."""
    values: dict[str, Counter] = defaultdict(Counter)
    for record in records:
        for field, value in record.items():
            values[field][str(value)] += 1
    return "; ".join(
        f"{field} " + ", ".join(f"{_shown(field, value)} ({_runs(count)})" for value, count in counted.items())
        for field, counted in values.items()
    )


def _shown(field: str, value: str) -> str:
    """A thinking value as the report shows it: a setting quoted as code, Whitney's own words as they are."""
    return value if field == "text" or value in PLAIN else f"`{value}`"


def _runs(count: int) -> str:
    return f"{count} run" if count == 1 else f"{count} runs"
