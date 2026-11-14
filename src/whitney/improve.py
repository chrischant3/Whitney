"""The improvement report: for each file under test, what its runs and its own text show is worth changing.

Every finding comes from a fixed rule; no model writes any of it (tasks/todo-phase5.md, Task 40).
docs/improvement-rules.md lists the rules, what fires each, what it advises and its source.

The report keeps two kinds of advice apart:

- **What the runs showed**: the template's counts. A finding from the runs
  needs more than one run of a scenario, and at least `min_conclusive` of the
  template's runs conclusive; with less, the report says "not enough runs to
  say". A file whose template held while a baseline failed has nothing to
  change. The one rule that reads the runs, `hook-to-load-skill`, never
  fires on the file's text alone.
- **What the file's text suggests**: the text rules (text_findings) read the
  file under test as the template's log staged it (`evaluation_assets`).
  Unless enough runs failed behind it, each is labelled a suggestion.

Text quoted from a project's files or logs goes in code spans no backtick
inside it can close (_quote), so it can't break the report's Markdown.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from whitney.report import TEMPLATE_VARIANT, Cell, EvalSummary, Record, records_from_log, summarise
from whitney.thresholds import MIN_CONCLUSIVE
from whitney.validation import BOM, STAGED, frontmatter_value, skill_description, skill_description_warnings

HEADING = "## How to improve the files under test"
RULES_DOC = "docs/improvement-rules.md"
INTRO = (
    f"Fixed rules, listed with their sources in `{RULES_DOC}`; no model wrote this section. "
    "What the runs showed is kept apart from what the file's text suggests."
)
READ_ON = "read 2026-11-11"
SOURCES = {
    "prompting": f"[Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), {READ_ON}",
    "skills": f"[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), {READ_ON}",
    "memory": f"[How Claude remembers your project](https://code.claude.com/docs/en/memory), {READ_ON}",
    "hooks": f"[Claude Code hooks reference](https://code.claude.com/docs/en/hooks), {READ_ON}",
    "plugins": f"[Kilo plugins](https://kilo.ai/docs/automate/extending/plugins), {READ_ON}",
    "spike": "Whitney's spike, `docs/spike-findings.md`, \"Phase 5 Task 40: hooks to enforce a skill\"",
}
# Rule id -> (what it found, its sources). The ids are stable, so a reader can look each up in RULES_DOC.
RULES = {
    "say-what-to-do": ("a rule says what not to do, and not what to do instead", ("prompting",)),
    "give-a-reason": ("a rule gives no reason", ("prompting",)),
    "no-shouting": ("capitals such as MUST and NEVER are used often", ("prompting",)),
    "whitney-validate": ("`whitney validate` warns about the skill's description", ("skills",)),
    "description-says-when": ("the skill's description doesn't say when to use it", ("skills",)),
    "always-loaded-size": ("an instruction file loaded in every session is over 200 lines", ("memory",)),
    "skill-body-size": ("the SKILL.md body is over 500 lines", ("skills",)),
    "one-level-deep": ("a skill's file other than SKILL.md links to another of its files", ("skills",)),
    # Its sources depend on the target, so each finding names them (HOOKS).
    "hook-to-load-skill": ("the skill's description is sound, and the skill still didn't load", ()),
}

ALWAYS_LOADED_LIMIT = 200  # lines; Claude Code's memory page: "target under 200 lines per CLAUDE.md file"
SKILL_BODY_LIMIT = 500  # lines; the skill authoring page: "Keep the SKILL.md body under 500 lines"
# A word or two in capitals can mark a hard limit; the prompting page says emphatic wording makes current
# models overtrigger. Three in one file is this report's line between the two: no source gives a number.
SHOUT_LIMIT = 3
QUOTED_ITEMS = 3  # lines quoted as one finding's evidence; the rest are counted
QUOTE_LIMIT = 200  # characters of a quoted line
# Whitney's own numbers, not a source's: below 4 runs one miss is too much chance, and under a quarter
# missed the skill loads often enough that a hook's cost (it runs as the user) may not be worth it.
HOOK_MIN_RUNS = 4  # runs that could tell whether the skill loaded
HOOK_MIN_MISSED = 0.25  # the share of them in which it didn't

FENCE = re.compile(r"\s*(```|~~~)")
ITEM = re.compile(r"(\s*)([-*+]|\d+[.)])\s+(.*)")
CODE_SPAN = re.compile(r"(`+).*?\1")
QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")  # double quotes only: a single one is often an apostrophe
FORBIDS = re.compile(r"[*_\s]*(never|don't|do not|avoid|no)\b", re.IGNORECASE)  # after any emphasis
ALTERNATIVE = re.compile(r"\b(instead|rather|unless|but|only|except)\b", re.IGNORECASE)
# A second clause starting with one of these verbs tells the agent what to do: "Don't use npm, use pnpm."
# A list of verbs, not a guess at any word, so a list of names ("never use eval, exec or compile") isn't one.
DOES_INSTEAD = re.compile(
    r"[,;]\s*(?:and |then |always |just )?(add|ask|call|check|choose|commit|copy|create|keep|leave|let|make|mark|move|"
    r"name|open|pass|pick|prefer|put|raise|read|return|run|say|send|set|stop|store|tell|try|update|use|wait|write)\b",
    re.IGNORECASE,
)
REASON = re.compile(r"\b(because|since|so|otherwise|which|why|in order to)\b|[:;(–—]", re.IGNORECASE)
LABEL = re.compile(r"^[*_]*\s*[:–—]")  # "**Never:** ...": a mark straight after the directing word labels it
# A bullet with one of these words directs the agent; one without, such as "The file under test: ...", describes.
# "No" directs only at the start ("No force pushes."); later it describes ("Python 3.11, no external services").
DIRECTS = re.compile(r"\b(never|always|must|should|shall|don't|do not|avoid)\b|^[*_\s]*no\b", re.IGNORECASE)
SHOUTED = re.compile(r"\b(MUST|NEVER|ALWAYS|CRITICAL|IMPORTANT|REQUIRED)\b")
SAYS_WHEN = re.compile(r"\b(when|whenever|before|after|if|while)\b", re.IGNORECASE)
SENTENCE_END = re.compile(r"[.!?][*_\"')]*(?:\s+|$)")  # emphasis or a quote can close after the stop
ABBREVIATION = re.compile(r"\b(e\.g|i\.e|etc|vs|cf)\.", re.IGNORECASE)  # a stop that ends no sentence
LINK = re.compile(r"\]\(([^)\s]+)\)")
SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")  # a skill name that can go into the hook's shell script and code as it is
DESCRIPTION_RULES = ("whitney-validate", "description-says-when")


@dataclass(frozen=True)
class Finding:
    """One thing to change in a file under test.

    Attributes:
        rule: the rule's id, a key of RULES.
        file: the project-relative path the finding is about.
        evidence: Markdown lines, each a quoted line of the file or the runs' counts.
        change: what to try.
        check: how to tell whether it worked; empty for a text rule, whose
            report says what the file's next run should show.
        blocks: Markdown shown after the finding's lines, such as a hook to add.
        sources: keys of SOURCES, where they differ from the rule's own, such as the one target's docs.
    """

    rule: str
    file: str
    evidence: tuple[str, ...]
    change: str
    check: str = ""
    blocks: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass
class _Item:
    line: int  # 1-based, in the file
    numbered: bool
    text: str  # the item's own, with the indented lines that continue it
    end: int  # the last line the item, or an item nested in it, reaches
    indent: int
    nested: str = ""  # the text of the items nested in it, which may give its reason or alternative


@dataclass
class _Template:
    """What an eval file's template log holds that the records don't."""

    files: dict[str, str] = field(default_factory=dict)  # as the template staged them
    target: str | None = None
    said: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # scenario -> (outcome, the check's explanation)


def text_findings(path: str, files: Mapping[str, str]) -> list[Finding]:
    """What the text rules find in the Markdown file at `path`, with `files` (path -> text) as the project held them.

    Empty for a file that isn't Markdown, such as `kilo.jsonc`, or isn't in
    `files`. The skill rules read the skill's other files in `files`, and
    validate's description warnings compare its description with the other skills' there.
    """
    text = files.get(path)
    if text is None or not path.lower().endswith(".md"):
        return []
    return [finding for rule in TEXT_RULES for finding in rule(path, text, files)]


def improvement_report(
    logs: Iterable[object], min_conclusive: float = MIN_CONCLUSIVE, records: list[Record] | None = None,
) -> str:
    """The report's section on how to improve each file under test, or "" if no log names its eval file.

    Args:
        logs: the Inspect logs the report's tables were made from. Their
            metadata gives each file's text as the template staged it, and the target.
        min_conclusive: the share of the template's runs that must be
            conclusive before the runs are said to show anything.
        records: the logs' records, if the caller has read them already; else they are read here.
    """
    logs = list(logs)
    if records is None:
        records = [record for log in logs for record in records_from_log(log)]
    templates = _templates(logs)
    by_file: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        if record.file:
            by_file[record.file.eval_file].append(record)
    sections = [
        _section(key, summary, by_file[key], templates.get(key, _Template()), min_conclusive)
        for key, summary in summarise(records).items()
        if summary.file
    ]
    return "\n".join([HEADING, "", INTRO, "", *sections]) if sections else ""


def improvement_failed(error: Exception) -> str:
    """The section in place of improvement_report's when building it raised `error`: one line naming the error.

    The section only advises, so the report and the gate go on without it.
    """
    return f"{HEADING}\n\nThis section couldn't be built: {_quote(f'{type(error).__name__}: {error}')}\n"


def _templates(logs: list) -> dict[str, _Template]:
    """Eval file -> what its template logs hold: the first one's staged files and target, and every run's explanation."""
    found: dict[str, _Template] = {}
    for log in logs:
        metadata = getattr(log.eval, "metadata", None) or {}  # a hand-built log in a test may have none
        assets = metadata.get("evaluation_assets")
        if metadata.get("variant") != TEMPLATE_VARIANT or "eval_file" not in metadata or not isinstance(assets, dict):
            continue
        template = found.setdefault(str(metadata["eval_file"]), _Template(dict(assets), metadata.get("target")))
        for sample in log.samples or []:
            score = next(iter((sample.scores or {}).values()), None)
            explanation = getattr(score, "explanation", None)
            if isinstance(explanation, str):
                template.said.setdefault(str(sample.id), []).append((score.answer, explanation))
    return found


def _section(key: str, s: EvalSummary, records: list[Record], template_log: _Template, min_conclusive: float) -> str:
    """One file's part of the report: its heading, what its runs showed, then what its text suggests."""
    source, files, target = s.file.source or "", template_log.files, template_log.target
    template = s.overall.get(TEMPLATE_VARIANT, Cell())
    why_not = _not_enough(s, template, min_conclusive)
    texts = text_findings(source, files)
    run_findings = [] if why_not or source not in files else _hook_findings(s, records, source, texts, target)
    failed = not why_not and template.failed > 0
    lines = [f"### {_heading(s)}", "", f"File under test: {_quote(source) if source else 'not recorded'} · eval file {_quote(key)}", ""]
    lines += ["**What the runs showed**", ""]
    if why_not:
        lines += [f"Not enough runs to say: {why_not}", ""]
    elif failed:
        lines += [f"The template failed in {template.failed} of {template.passed + template.failed} conclusive runs:", ""]
        lines += [
            f"- {_quote(name)}{_pressure(records, name)}: {_count(cell)}{_said(template_log, name, s.file.labels[1])}"
            for name, cell in _failing(s)
        ] + [""]
    else:
        held = _held(s, template)
        if held is None and not run_findings:
            return "\n".join([*lines, _nothing_to_change(s, template), ""])
        lines += [held or f"The template held in all {template.passed} conclusive runs.", ""]
    for finding in run_findings:
        lines += _render(finding, "")
    lines += ["**What the file's text suggests**", ""]
    lines += _text_part(source, files, texts, failed, _rerun(key, s, template))
    return "\n".join(lines)


def _heading(s: EvalSummary) -> str:
    """The section's heading: the file's title on one line, with no `#` or backtick to start Markdown in it."""
    return " ".join(s.file.heading.split()).replace("#", "\\#").replace("`", "'")


def _not_enough(s: EvalSummary, template: Cell, min_conclusive: float) -> str | None:
    """Why the template's runs can't bear a finding, and what to do about it, or None if they can."""
    runs = template.passed + template.failed + template.inconclusive
    conclusive = template.passed + template.failed
    if not runs:
        return "the template has no runs here."
    ran = [cells[TEMPLATE_VARIANT] for cells in s.rows.values() if TEMPLATE_VARIANT in cells]
    if max(c.passed + c.failed + c.inconclusive for c in ran) <= 1:
        return "each scenario ran once in the template, and one run can pass or fail by chance. Run it again with `--epochs 3` or more."
    if conclusive / runs < min_conclusive:
        return (
            f"only {conclusive} of the template's {runs} runs were conclusive, below the minimum {min_conclusive:.0%}. "
            "The tables above say how the others ended; a run that ends early proves nothing either way."
        )
    return None


def _failing(s: EvalSummary) -> list[tuple[str, Cell]]:
    """The scenarios and trigger prompts that failed in the template, by name."""
    return [
        (name, cells[TEMPLATE_VARIANT])
        for name, cells in sorted(s.rows.items())
        if TEMPLATE_VARIANT in cells and cells[TEMPLATE_VARIANT].failed
    ]


def _pressure(records: list[Record], scenario: str) -> str:
    """" (pressure: time)" for a scenario its eval file puts under pressure, else ""."""
    pressure = [r.metadata.get("pressure") for r in records if r.scenario == scenario][0]  # a failing scenario has a run
    return f" (pressure: {_quote(pressure)})" if pressure not in (None, "none") else ""


def _said(template_log: _Template, scenario: str, failing: str) -> str:
    """What the check said of the scenario's first failing run in the template, quoted; "" if the log has nothing."""
    said = next((text for outcome, text in template_log.said.get(scenario, []) if outcome == failing), None)
    return f". The check said: {_quote(_home_as_tilde(said))}" if said and said.strip() else ""


def _home_as_tilde(text: str) -> str:
    """`text` with the user's home folder written `~`, so a quoted path doesn't name the user who ran it."""
    home = os.path.expanduser("~").rstrip("/")
    if not home or home == "~":  # no home to hide: os.path.expanduser leaves "~" as it is
        return text
    return re.sub(re.escape(home) + r"(?![\w.-])", "~", text)


def _count(cell: Cell) -> str:
    """passed/conclusive, with the inconclusive runs counted if there were any."""
    shown = f"{cell.passed}/{cell.passed + cell.failed}"
    return f"{shown} ({cell.inconclusive} inconclusive)" if cell.inconclusive else shown


def _baselines(s: EvalSummary) -> list[tuple[str, Cell]]:
    return [(variant, cell) for variant, cell in sorted(s.overall.items()) if s.roles.get(variant) == "baseline"]


def _held(s: EvalSummary, template: Cell) -> str | None:
    """For a template that held: why the runs can't credit the file, or None if a baseline failed and they can."""
    baselines = _baselines(s)
    if any(cell.failed for _, cell in baselines):
        return None
    if baselines:
        why = "no baseline failed (" + ", ".join(f"{_quote(v)} {_count(cell)}" for v, cell in baselines) + ")"
    else:
        why = "no baseline ran"
    return f"The template held in all {template.passed} conclusive runs, but {why}, so the runs can't show the file made the difference."


def _nothing_to_change(s: EvalSummary, template: Cell) -> str:
    failed = ", ".join(f"{_quote(v)} failed {cell.failed} of {cell.passed + cell.failed}" for v, cell in _baselines(s) if cell.failed)
    return (
        f"Nothing to change: the template held in all {template.passed} conclusive runs and {failed}, "
        "so the file is doing its job. The text rules aren't applied to a file its runs show working."
    )


def _rerun(key: str, s: EvalSummary, template: Cell) -> str:
    """How to tell whether a change to the file's text worked: what its next run should show."""
    failing = [name for name, _ in _failing(s)]
    then = f", and {', '.join(map(_quote, failing))} should fail less often" if failing else ""
    return f"Run {_quote(key)} again with as many epochs: the template, now {_count(template)}, should hold at least as often{then}."


def _text_part(source: str, files: dict[str, str], texts: list[Finding], failed: bool, rerun: str) -> list[str]:
    """The "What the file's text suggests" lines: each text finding, with `rerun` as how to tell, or why there are none."""
    if not source:
        return ["The log doesn't name the file under test, so the text rules couldn't read it.", ""]
    if not source.lower().endswith(".md"):
        return [f"The text rules read Markdown instruction files only, so they have nothing to say about {_quote(source)}.", ""]
    if source not in files:
        return [f"The template's log holds no copy of {_quote(source)}, so the text rules couldn't read it.", ""]
    if not texts:
        return ["The text rules found nothing to change.", ""]
    lines = [
        "These come from the file's text, not from the runs: places to start on the failures above, not their proven cause."
        if failed else "No failing run is behind these, so each is a suggestion: the file's text shows it, the runs don't.",
        "",
    ]
    for finding in texts:
        lines += _render(replace(finding, check=rerun), "" if failed else "Suggestion: ")
    return lines


def _render(finding: Finding, label: str) -> list[str]:
    """A finding as Markdown: its rule and file, then its evidence, what to try, how to tell and its sources."""
    title, sources = RULES[finding.rule]
    sources = finding.sources or sources
    lines = [f"**{label}`{finding.rule}`** in {_quote(finding.file)}: {title}.", ""]
    lines += [f"- Evidence: {evidence}" for evidence in finding.evidence]
    lines += [f"- Try: {finding.change}", f"- How to tell: {finding.check}"]
    lines += [f"- Source: {'; '.join(SOURCES[name] for name in sources)}.", ""]
    for block in finding.blocks:
        lines += [block, ""]
    return lines


# --- the text rules ---------------------------------------------------------------------------


def _frontmatter_end(lines: list[str]) -> int:
    """The index of the first line after the frontmatter, or 0 if the file has none."""
    if not lines or lines[0].removeprefix(BOM).strip() != "---":
        return 0
    return next((i + 1 for i, line in enumerate(lines[1:], 1) if line.rstrip() == "---"), 0)


def _body(text: str) -> list[tuple[int, str]]:
    """The file's lines after its frontmatter and outside fenced code, numbered from 1."""
    lines = text.splitlines()
    start = _frontmatter_end(lines)
    body, fenced = [], False
    for number, line in enumerate(lines[start:], start + 1):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            body.append((number, line))
    return body


def _items(text: str) -> list[_Item]:
    """The file's list items, each with the indented lines straight after it that continue it.

    An item indented under another, with no gap between, is an item of its
    own and also part of the text nested in each item it sits under.
    """
    items: list[_Item] = []
    chain: list[_Item] = []  # the last item read, and the items it is nested in
    for number, line in _body(text):
        match = ITEM.fullmatch(line)
        if match:
            indent = len(match.group(1))
            chain = [item for item in chain if item.end == number - 1 and item.indent < indent]
            item = _Item(number, match.group(2)[0].isdigit(), match.group(3).strip(), number, indent)
            for parent in chain:
                parent.nested, parent.end = f"{parent.nested} {item.text}", number
            items.append(item)
            chain.append(item)
        elif chain and chain[-1].end == number - 1 and line.strip() and line[0].isspace():
            chain[-1].text = f"{chain[-1].text} {line.strip()}"
            for parent in chain[:-1]:
                parent.nested = f"{parent.nested} {line.strip()}"
            for item in chain:
                item.end = number
    return items


def _plain(text: str) -> str:
    """`text` with each code span and double-quoted passage replaced by a word: a word inside them directs nothing."""
    return QUOTED.sub("quote", CODE_SPAN.sub("code", text))


def _sentences(text: str) -> int:
    return len([part for part in SENTENCE_END.split(ABBREVIATION.sub("abbreviation", _plain(text))) if part.strip()])


def _evidence(lines: list[tuple[int, str]]) -> tuple[str, ...]:
    """Up to QUOTED_ITEMS lines, quoted with their numbers, and a count of the rest."""
    shown = [f"line {number}: {_quote(text)}" for number, text in lines[:QUOTED_ITEMS]]
    more = len(lines) - QUOTED_ITEMS
    return (*shown, f"and {more} more like {'it' if more == 1 else 'them'}") if more > 0 else tuple(shown)


def _only_forbids(item: _Item) -> bool:
    """An item that starts with a prohibition and, with what is nested in it, is one sentence naming nothing to do instead."""
    whole = _plain(f"{item.text} {item.nested}")
    return bool(
        FORBIDS.match(_plain(item.text)) and _sentences(whole) == 1
        and not ALTERNATIVE.search(whole) and not DOES_INSTEAD.search(whole)
    )


def _say_what_to_do(path: str, text: str, files: Mapping[str, str]) -> list[Finding]:
    """A list item that only forbids (_only_forbids); the code and quotes in it are left out (_plain)."""
    hits = [(item.line, item.text) for item in _items(text) if _only_forbids(item)]
    return [Finding("say-what-to-do", path, _evidence(hits), (
        "add what to do in its place, as well as what not to do."
    ))] if hits else []


def _no_reason(item: _Item) -> bool:
    """A bullet whose own text directs (DIRECTS) and which, with what is nested in it, is one sentence giving no reason after the directing word."""
    directs = DIRECTS.search(_plain(item.text))
    if item.numbered or not directs:
        return False
    after = LABEL.sub("", _plain(item.text)[directs.end():])
    return _sentences(f"{item.text} {item.nested}") == 1 and not REASON.search(f"{after} {_plain(item.nested)}")


def _give_a_reason(path: str, text: str, files: Mapping[str, str]) -> list[Finding]:
    """A bullet that directs with no reason (_no_reason). Numbered items are steps, which need none."""
    hits = [(item.line, item.text) for item in _items(text) if _no_reason(item)]
    return [Finding("give-a-reason", path, _evidence(hits), (
        "say why the rule matters, so the model can apply it to cases the rule doesn't name."
    ))] if hits else []


def _no_shouting(path: str, text: str, files: Mapping[str, str]) -> list[Finding]:
    """SHOUT_LIMIT or more MUST, NEVER and the like, in capitals and outside code."""
    # A word in a code span is a name, such as a constant, not emphasis.
    hits = [(number, line.strip()) for number, line in _body(text) if SHOUTED.search(CODE_SPAN.sub("", line))]
    count = sum(len(SHOUTED.findall(CODE_SPAN.sub("", line))) for _, line in hits)
    if count < SHOUT_LIMIT:
        return []
    return [Finding("no-shouting", path, (f"{count} such words", *_evidence(hits)), (
        "write them in ordinary case, and keep capitals for the one or two hard limits: current models follow "
        "ordinary wording, and emphatic wording makes them overtrigger."
    ))]


def _validate_warnings(path: str, text: str, files: Mapping[str, str]) -> list[Finding]:
    """`whitney validate`'s warnings about this skill's description, as validate words them.

    Validate names a near-duplicate pair at the later skill's path only, so
    a warning there that names this skill counts for this one too.
    """
    skills = {name: body for name, body in files.items() if STAGED["skill"].fullmatch(name)}
    findings = []
    for w in skill_description_warnings(skills, Path()):
        if w.file == Path(path):
            evidence = f"`whitney validate`: {_quote(w.message)}"
        elif f" {path}'s " in w.message:
            evidence = f"`whitney validate`, on {_quote(w.file.as_posix())}: {_quote(w.message)}"
        else:
            continue
        findings.append(Finding("whitney-validate", path, (evidence,), "fix what the warning names."))
    return findings


def _description_says_when(path: str, text: str, files: Mapping[str, str]) -> list[Finding]:
    """A skill's description with no word saying when to use it. With no description at all, validate warns instead."""
    description = skill_description(text) if STAGED["skill"].fullmatch(path) else None
    if not description or SAYS_WHEN.search(description):
        return []
    return [Finding("description-says-when", path, (f"description: {_quote(description)}",), (
        "add when to use the skill, naming the requests and files it covers, such as \"Use when the user asks for …\". "
        "The model picks a skill by its description."
    ))]


def _always_loaded_size(path: str, text: str, files: Mapping[str, str]) -> list[Finding]:
    """An instruction file over ALWAYS_LOADED_LIMIT lines: not a skill, agent or command, nor a rule scoped by `paths`."""
    if any(pattern.fullmatch(path) for pattern in STAGED.values()) or frontmatter_value(text, "paths") is not None:
        return []
    lines = len(text.splitlines())
    if lines <= ALWAYS_LOADED_LIMIT:
        return []
    return [Finding("always-loaded-size", path, (f"{lines} lines, loaded in every session",), (
        "keep what every task needs, and move instructions for one kind of task, or one part of the codebase, "
        "into a skill, which loads only when it is needed, or on Claude Code into a rules file with `paths`."
    ))]


def _skill_body_size(path: str, text: str, files: Mapping[str, str]) -> list[Finding]:
    """A SKILL.md body over SKILL_BODY_LIMIT lines; the advice says whether the skill has other files to move detail to."""
    if not STAGED["skill"].fullmatch(path):
        return []
    lines = text.splitlines()
    body = len(lines) - _frontmatter_end(lines)  # code included: all of the body is loaded with the skill
    if body <= SKILL_BODY_LIMIT:
        return []
    folder = posixpath.dirname(path)
    where = "files beside it, such as `references/<topic>.md`, each linked from SKILL.md with when to read it"
    if any(name != path and name.startswith(f"{folder}/") for name in files):
        change = f"move more of the detail into {where}."
    else:
        change = f"the skill holds everything in SKILL.md and has no other file: move detail into {where}."
    return [Finding("skill-body-size", path, (f"the body is {body} lines",), change)]


def _one_level_deep(path: str, text: str, files: Mapping[str, str]) -> list[Finding]:
    """A Markdown file of the skill's, other than SKILL.md, with a link to another of the skill's files."""
    if not STAGED["skill"].fullmatch(path):
        return []
    folder = posixpath.dirname(path)
    hits = []
    for name in sorted(files):
        if name == path or not name.startswith(f"{folder}/") or not name.lower().endswith(".md"):
            continue
        for number, line in _body(files[name]):
            linked = {posixpath.normpath(posixpath.join(posixpath.dirname(name), t)) for t in LINK.findall(line)}
            if any(t in files and t.startswith(f"{folder}/") and t not in (name, path) for t in linked):
                hits.append(f"{_quote(name)} line {number}: {_quote(line.strip())}")
    return [Finding("one-level-deep", path, tuple(hits), (
        "link each file from SKILL.md directly, with when to read it: a model may read only part of a file "
        "it reaches through another linked file."
    ))] if hits else []


TEXT_RULES: tuple[Callable[[str, str, Mapping[str, str]], list[Finding]], ...] = (
    _say_what_to_do, _give_a_reason, _no_shouting, _validate_warnings, _description_says_when,
    _always_loaded_size, _skill_body_size, _one_level_deep,
)


# --- rule 3: a hook to enforce a skill -------------------------------------------------------


def _hook_findings(s: EvalSummary, records: list[Record], source: str, texts: list[Finding], target: str | None) -> list[Finding]:
    """`hook-to-load-skill`, when the skill's description is sound and the template's runs show it often didn't load.

    The description is sound when neither validate's warnings nor
    `description-says-when` speak about it. The runs counted (_could_tell)
    are the template's, outside known gaps, where the skill should have
    loaded and the run says whether it did. At least HOOK_MIN_RUNS of them
    must, and in at least HOOK_MIN_MISSED of them it didn't. The caller has
    already judged that the runs are enough to say (_not_enough).
    """
    if not s.file.under_test or s.file.under_test[0] != "skill" or target not in HOOKS:
        return []
    if any(f.rule in DESCRIPTION_RULES for f in texts):
        return []
    runs = _could_tell(records, s.file.labels[0], s.file.labels[1])
    missed = [name for name, loaded in runs if not loaded]
    if len(runs) < HOOK_MIN_RUNS or len(missed) < HOOK_MIN_MISSED * len(runs):
        return []
    name = s.file.under_test[1]
    prompts = sorted({r.scenario for r in records if r.variant == TEMPLATE_VARIANT and r.trigger == "positive"})
    retry = ", ".join(_quote(n) for n in dict.fromkeys([*prompts, *sorted(missed)]))
    hook = HOOKS[target]
    filled = name if SAFE_NAME.fullmatch(name) else "<skill>"
    return [Finding(
        "hook-to-load-skill", source,
        (
            f"the skill {_quote(name)} loaded in {len(runs) - len(missed)} of {len(runs)} runs that could tell "
            "(the template's scenario runs and its positive trigger prompts)",
            "neither validate's description warnings nor `description-says-when` speak about its description, "
            "so the description alone can't be relied on to load it",
        ),
        f"**not tested by Whitney.** {hook['change']}",
        f"Add the hook, then run {retry} in {hook['harness']}, since a Whitney run leaves the hook out: "
        "the skill should load in each.",
        tuple(block(filled) for block in hook["blocks"]),
        hook["sources"],
    )]


def _could_tell(records: list[Record], passing: str, failing: str) -> list[tuple[str, bool]]:
    """(scenario, whether the skill loaded) for each template run outside known gaps where it should have.

    A scenario's run says so in its score (Record.use); a positive trigger
    prompt's passes when the skill loads. A negative prompt is left out: the
    skill shouldn't load for it.
    """
    runs = []
    for r in records:
        if r.variant != TEMPLATE_VARIANT or r.metadata.get("known_gap"):
            continue
        if r.trigger == "positive" and r.outcome in (passing, failing):
            runs.append((r.scenario, r.outcome == passing))
        elif r.trigger is None and r.use in ("used", "unused"):
            runs.append((r.scenario, r.use == "used"))
    return runs


def _fenced(language: str, text: str) -> str:
    """`text` in a fenced code block. Its text is the hook's, with a SAFE_NAME, so it holds no backtick to close the fence."""
    return f"```{language}\n{text}\n```"


def _claude_settings(name: str) -> str:
    """The `.claude/settings.json` entry that runs the reminder script (tasks/todo-phase5.md, Task 40)."""
    return "`.claude/settings.json`:\n\n" + _fenced("json", (
        '{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", '
        f'"command": "sh \\"$CLAUDE_PROJECT_DIR\\"/.claude/hooks/{name}-reminder.sh"}}]}}]}}}}'
    ))


def _claude_script(name: str) -> str:
    """The reminder script: one line naming the skill when the prompt is one it covers."""
    return f"`.claude/hooks/{name}-reminder.sh`:\n\n" + _fenced("sh", (
        "#!/bin/sh\n"
        "if grep -qiE '<words from the trigger prompts>'; then\n"
        f'  echo "This project has a {name} skill, and this request is one it covers. '
        f'Load it with the Skill tool (skill: {name}) before you answer, and follow it."\n'
        "fi"
    ))


def _kilo_plugin(name: str) -> str:
    """The spike's reminder plugin, without the lines that recorded what it saw."""
    return f"`.kilo/plugin/{name}-reminder.ts`:\n\n" + _fenced("ts", (
        "export default {\n"
        f'  id: "{name}-reminder",\n'
        "  server: async () => {\n"
        "    let needsSkill = false\n"
        "    return {\n"
        '      "chat.message": async (input: any, output: any) => {\n'
        '        const text = (output?.parts ?? []).filter((p: any) => p?.type === "text").map((p: any) => p.text).join(" ")\n'
        "        needsSkill = /<words from the trigger prompts>/i.test(text)\n"
        "      },\n"
        '      "experimental.chat.system.transform": async (input: any, output: any) => {\n'
        f'        if (needsSkill) output.system.push("This project has a {name} skill, and this request is one it covers. '
        f'Load it with the skill tool (name: {name}) before you answer, and follow it.")\n'
        "      },\n"
        "    }\n"
        "  },\n"
        "}"
    ))


# The hook advised on each target, as Christopher confirmed it on 2026-11-11 (tasks/todo-phase5.md, Task 40).
HOOKS: dict[str, dict] = {
    "claude-code": {
        "harness": "Claude Code itself",
        "change": (
            "Add a `UserPromptSubmit` command hook to the project's `.claude/settings.json`, below, which prints one line "
            "naming the skill when the prompt is one the skill covers; replace `<words from the trigger prompts>` with "
            "words from the prompts that should load it. The stricter step, if that isn't enough, is a `PreToolUse` hook "
            "that refuses other tools until `Skill` is called. A hook is a shell command Claude Code runs as the user, "
            "with the API key in its environment, and a `claude -p` run starts it without asking. Whitney stages no "
            "hooks, so a Whitney run leaves the hook out. In Whitney's spike (Claude Code 2.1.322, Haiku, one skill "
            "and one prompt) the skill loaded in 1 of 6 runs with no hook, and in 7 of 7 with a `UserPromptSubmit` "
            "or `SessionStart` reminder."
        ),
        "blocks": (_claude_settings, _claude_script),
        "sources": ("hooks", "spike"),
    },
    "kilo": {
        "harness": "Kilo itself, without `--pure`",
        "change": (
            "On Kilo a hook is a plugin: add the file below to the project's `.kilo/plugin/`, and replace "
            "`<words from the trigger prompts>` with words from the prompts that should load the skill. Its "
            "`chat.message` hook notes that the prompt is one the skill covers, and its "
            "`experimental.chat.system.transform` hook adds the same line to the system prompt. Kilo marks that "
            "hook experimental, so it may change. Whitney's spike (Kilo 7.8.27, Haiku) didn't show the reminder "
            "raising how often a skill loads: the skill loaded in 3 of 3 runs with it, but also in the one run "
            "where the plugin didn't run. The stricter step is a `tool.execute.before` hook that refuses other "
            "tools until `skill` is called, which the spike did see refuse. A plugin is code that runs inside "
            "Kilo's own process, where the Kilo login can be read, and it can change what the model is sent. "
            "Whitney runs Kilo with `--pure`, which turns plugins off, so a Whitney run leaves the plugin out."
        ),
        "blocks": (_kilo_plugin,),
        "sources": ("plugins", "spike"),
    },
}


def _quote(text: str) -> str:
    """`text` on one line in a code span no backtick inside it can close, so nothing in it is read as Markdown."""
    text = " ".join(str(text).split())
    if not text:
        return "(empty)"
    if len(text) > QUOTE_LIMIT:
        text = text[:QUOTE_LIMIT] + "…"
    runs = re.findall(r"`+", text)
    if not runs:
        return f"`{text}`"
    fence = "`" * (max(map(len, runs)) + 1)
    return f"{fence} {text} {fence}"
