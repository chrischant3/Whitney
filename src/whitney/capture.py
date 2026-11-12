"""What a run sent to the model, and the thinking level it set (Phase 5 Task 31).

A run with capture turned on (`whitney run --capture-prompt`) keeps a
`sent` entry in its record under agent.RUN_STORE_KEY: the body of the
run's first model call in full (up to MAX_STORED_BODY), then each request's
method, path (without its query) and size, and for each later model call
whether its system prompt and tools differ from the first's. It also says,
for each of the texts it was given to look for (the eval file's `source`,
see texts_to_find), where it was found: in the first model call, and in
which field and under which line, or only in a later request, which holds
what the agent read as well as what the harness sent. Only the first counts
as the text reaching the model as an instruction.

The run's model call is a request on a messages or chat path whose body
names the run's model (Capture). Anything else, such as Kilo's model list,
Claude Code's `HEAD /api/hello`, or a body some other local process posted
to the proxy without the run's model in it, is counted and never searched.

Header values are never seen here: the proxy (capture_proxy) hands over
the body only. A local run reads the requests through that proxy; a Docker
run reads Inspect's own log of the bridged calls (requests_from_inspect_log).
The agents redact the whole record (agent.redact_run) before it is stored.

A run with capture or `--thinking` keeps a `thinking` entry too: the
thinking level the run asked for, as the harness was told it, and the level
the first model call carried, never a bare on or off (thinking_record). The
thinking text stays where each harness writes it, in the run's events.

Evidence for every field read here: docs/spike-findings.md, "Phase 5 Task
31", and the requests in tests/data/prompt-capture/.
"""

import json
import re
import threading
import zlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from whitney.evalfile import under_test
from whitney.project_files import CLAUDE_SETTINGS, CONFIG_PATHS, claude_code_instructions, staged_imports, without_block_comments
from whitney.validate import frontmatter_value

VIA_PROXY = "proxy"
VIA_INSPECT_LOG = "inspect-log"
MIB = 1024 * 1024
# The most of one request's body a capture keeps and searches: far above the 70 to 90 KB a real model call
# is (docs/results/2026-11-12-prompt-capture-runs.md), so only a body no harness sends is cut.
MAX_STORED_BODY = 16 * MIB
# The paths a model call is sent to: Anthropic's Messages API and the OpenAI format's chat completions,
# after whatever base path the provider has. /v1/messages/count_tokens, say, is not one.
MODEL_CALL_PATHS = ("/messages", "/chat/completions")
FIRST, LATER = "first", "later"  # where a text was found: the run's first model call, or only a later request
SYSTEM_PROMPT, MESSAGES, ELSEWHERE = "system prompt", "messages", "elsewhere in the request"
HEADING_MAX = 200  # characters of the line above a found text that are kept
NO_MODEL_CALL = "no model call was captured"
NOT_SET = "no thinking level"
NOT_CAPTURED = "not captured"
LEVEL_NOT_RECORDED = "not recorded (no --capture-prompt)"
NO_MODEL_REQUEST = NO_MODEL_CALL
LEVEL_CUT = "not read (the first model call's body was over the cap)"
NO_THINKING_SETTING = "no thinking setting in the request"
# The request fields that carry a thinking level: Anthropic's `thinking`, and the OpenAI format's `reasoning`
# that Kilo sent through the wrong Gateway address (docs/results/2026-11-12-capture-followups.md).
THINKING_FIELDS = ("thinking", "reasoning", "reasoning_effort")
TEXT_IN_EVENTS = "in the events"
TEXT_WITHHELD = "withheld: the thinking blocks are empty"
NO_TEXT = "none in the events"
ATTACHMENT = "attachment://"  # how Inspect's transcript refers to a long string it keeps aside
# The budget a Docker run with --thinking asks Inspect for: Claude Code's own default on the host
# (docs/spike-findings.md, "Extended thinking"), so a Docker run asks for what a local one gets.
DOCKER_THINKING_TOKENS = 31999
KILO_THINKING = ("--variant", "high", "--thinking")  # the variant sets the level; --thinking puts the text in the stream
CLAUDE_CODE_THINKING = {"showThinkingSummaries": True}  # in --settings: the thinking text in the stream
DECODED = frozenset({"gzip", "x-gzip", "deflate"})  # the content encodings zlib reads (gzip, or deflate's zlib wrapper)
PLAIN = frozenset({"", "identity"})
JSON_STRING = re.compile(rb'"(?:[^"\\]|\\.)*"')
MODEL_FIELD = re.compile(rb'"model"\s*:\s*("(?:[^"\\]|\\.)*")')
# An absolute path's folders, which the heading of a found text gives as "…/": they are the run's temporary
# project folder, different in every run, so the report can count the same heading across runs.
PATH_FOLDERS = re.compile(r"(?<![\w.~])/(?:[^\s/]+/)+")
STRUCTURAL = frozenset({"type", "role"})  # keys whose values label a block or message rather than say anything


def normalised(text: str) -> str:
    """`text` with line endings made \\n, each line's trailing spaces dropped and the outer blank lines removed."""
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()


def request_text(value) -> str:
    """Every string in a JSON value, joined by newlines: a request's text, whatever its format."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(request_text(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(request_text(item) for item in value)
    return ""


def _content_text(value) -> str:
    """Like request_text, but without the values of STRUCTURAL keys, so the line above a text is the harness's, not a `type`."""
    if isinstance(value, dict):
        return "\n".join(_content_text(item) for name, item in value.items() if name not in STRUCTURAL)
    if isinstance(value, list):
        return "\n".join(_content_text(item) for item in value)
    return request_text(value)


def stored_path(path: str | None) -> str | None:
    """A request's path as it is stored: without its query, whose values can hold a key (`?api_key=...`)."""
    return None if path is None else path.partition("?")[0]


def model_names(model: str) -> set[str]:
    """The names a harness may put in a request's `model` for the run's `model`: it and each shorter `/` suffix.

    kilo/anthropic/claude-haiku-4.5 is sent as anthropic/claude-haiku-4.5
    on the Gateway, and anthropic/claude-haiku-4-5 as claude-haiku-4-5 by
    Claude Code and by Kilo with a provider key (tests/data/prompt-capture/).
    """
    parts = model.split("/")
    return {"/".join(parts[start:]) for start in range(len(parts))}


@dataclass(frozen=True)
class _Read:
    """What a capture could read of one body: the decoded text it keeps (None if it couldn't), and why not."""

    data: bytes | None
    cut: bool
    encoding: str | None = None
    not_searched: str | None = None


def _read(body: bytes, size: int, encoding: str | None) -> _Read:
    """`body` (the first MAX_STORED_BODY bytes of `size`) decoded, and cut to MAX_STORED_BODY.

    A gzip or deflate body is decoded, never to more than MAX_STORED_BODY
    and a byte, so a small body can't decode into an unbounded one. Any
    other encoding is left as it is and not searched.
    """
    named = (encoding or "").strip().lower()
    if named in PLAIN:
        return _Read(body[:MAX_STORED_BODY], size > MAX_STORED_BODY)
    if named not in DECODED:
        return _Read(None, False, named, f"compressed ({named}), not searched")
    try:
        data = zlib.decompressobj(zlib.MAX_WBITS | 32).decompress(body, MAX_STORED_BODY + 1)  # 32: a gzip or zlib header
    except zlib.error:
        return _Read(None, False, named, f"compressed ({named}), could not be decoded")
    return _Read(data[:MAX_STORED_BODY], size > len(body) or len(data) > MAX_STORED_BODY, named)


def _partial_text(data: bytes) -> str:
    """Every whole JSON string in `data`, the start of a body that was cut, decoded and joined by newlines."""
    strings = []
    for match in JSON_STRING.finditer(data):
        try:
            strings.append(json.loads(match.group()))
        except ValueError:  # a string with an escape json can't read, or bytes that aren't UTF-8
            continue
    return "\n".join(strings)


def _partial_model(data: bytes) -> str | None:
    """The `model` a cut body names, if its kept start has one."""
    match = MODEL_FIELD.search(data)
    if match is None:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


class Capture:
    """A run's `sent` entry, built one request at a time; safe to add to from several threads.

    Only the body of the run's first model call is kept. A model call is a
    JSON object with a `messages` list (the shape of both Anthropic's and the
    OpenAI format's requests), sent to a path ending in one of
    MODEL_CALL_PATHS, whose `model` is one of model_names(model). Anything
    else is listed by its method, path and size only, counted as not the
    run's model call, and never searched.

    Args:
        via: VIA_PROXY or VIA_INSPECT_LOG, where the requests came from.
        texts: label -> text to look for in every model call, after
            normalising both (texts_to_find). An empty text is left out.
        redact: applied to each request's text before it is searched. The
            texts come redacted (Inspect logs an agent's arguments, and the
            task passes them as one), so a request is redacted the same way.
        model: the run's model. None matches any model, which Inspect's log
            needs: its bridge answers every call with the run's model, so
            the first model event is the first call (from_inspect_log).
    """

    def __init__(self, via: str, texts: Mapping[str, str], redact: Callable[[str], str] = str, model: str | None = None) -> None:
        self._via = via
        self._redact = redact
        self._models = model_names(model) if model else None
        self._texts = {label: normalised(text) for label, text in texts.items() if normalised(text)}
        self._lock = threading.Lock()
        self._requests: list[dict] = []
        self._first: dict | None = None
        self._first_at: int | None = None
        self._first_cut: dict | None = None
        self._in_first: dict[str, dict] = {}
        self._later: dict[str, int] = {}
        self._not_model_calls = 0
        self._not_searched = 0

    @property
    def kept_bytes(self) -> int:
        """How much of a body the capture keeps: what a proxy hands it of a longer one (MAX_STORED_BODY)."""
        return MAX_STORED_BODY

    def add(self, body: bytes, method: str | None = None, path: str | None = None, *, size: int | None = None, encoding: str | None = None) -> None:
        """Record one request.

        Args:
            body: its body as it came, or its first kept_bytes if it was longer.
            method, path: as the request named them; None from Inspect's log.
            size: the body's whole length, if `body` is only its start.
            encoding: its Content-Encoding header, if any.
        """
        size = len(body) if size is None else size
        read = _read(body, size, encoding)
        entry: dict = {"method": method, "path": stored_path(path), "bytes": size}
        with self._lock:
            index = len(self._requests)
            if read.data is None:
                entry |= {"model_request": False, "encoding": read.encoding, "not_searched": read.not_searched}
                self._not_searched += 1
            else:
                self._add_read(entry, read, path, index)
            if path is not None and "?" in path:
                entry["query_removed"] = True
            self._requests.append(entry)

    def _add_read(self, entry: dict, read: _Read, path: str | None, index: int) -> None:
        """Classify and search one body the capture could read; called with the lock held."""
        parsed = None if read.cut else _json_object(read.data)
        named = _partial_model(read.data) if read.cut else (parsed or {}).get("model")
        has_messages = b'"messages"' in read.data if read.cut else isinstance((parsed or {}).get("messages"), list)
        call = has_messages and self._is_run_model(named) and (path is None or stored_path(path).endswith(MODEL_CALL_PATHS))
        entry["model_request"] = call
        if read.cut:
            entry["cut"] = True
        if read.encoding:
            entry["encoding"] = read.encoding
        if not call:
            self._not_model_calls += 1
            return
        if self._first_at is None:
            self._first_at = index
            if read.cut:
                self._first_cut = {"bytes": entry["bytes"], "searched_bytes": len(read.data)}
            else:
                self._first = parsed
            self._in_first = self._where(parsed, read.data)
            return
        if parsed is not None and self._first is not None:
            entry["system_differs"] = _system(parsed) != _system(self._first)
            entry["tools_differ"] = parsed.get("tools") != self._first.get("tools")
        text = normalised(self._redact(request_text(parsed) if parsed is not None else _partial_text(read.data)))
        for label, wanted in self._texts.items():
            if label not in self._in_first and label not in self._later and wanted in text:
                self._later[label] = index

    def _is_run_model(self, named) -> bool:
        return self._models is None or (isinstance(named, str) and named in self._models)

    def _where(self, parsed: dict | None, data: bytes) -> dict[str, dict]:
        """Where in the first model call each text is: label -> {"field", "heading"}, for the labels found."""
        if parsed is None:  # cut: its fields can't be told apart, so the kept start is searched as one
            kept = _partial_text(data)
            parts = [(None, kept, kept)]
        else:
            rest = {name: value for name, value in parsed.items() if name not in ("system", "messages")}
            fields = [(SYSTEM_PROMPT, _system(parsed)), (MESSAGES, _not_system(parsed)), (ELSEWHERE, rest)]
            parts = [(name, request_text(value), _content_text(value)) for name, value in fields]
        found: dict[str, dict] = {}
        for name, raw, content in parts:
            text, content = normalised(self._redact(raw)), normalised(self._redact(content))
            for label, wanted in self._texts.items():
                if label not in found and wanted in text:
                    at = content.find(wanted)
                    found[label] = {"field": name, "heading": _heading(content, at) if at >= 0 else None}
        return found

    def record(self) -> dict:
        """The `sent` entry as it stands.

        Holds via, requests, first_request (an index or None), first_body
        (None if there was no model call, or its body was cut), first_body_cut
        ({"bytes", "searched_bytes"} or None), not_model_calls, not_searched
        (requests whose compressed body couldn't be read) and found: label ->
        {"in": FIRST, "field", "heading"}, {"in": LATER, "request"} or
        {"in": None}, with "not_searched" saying why when the first model call
        wasn't searched whole.
        """
        with self._lock:
            return json.loads(json.dumps({
                "via": self._via,
                "requests": self._requests,
                "first_request": self._first_at,
                "first_body": self._first,
                "first_body_cut": self._first_cut,
                "not_model_calls": self._not_model_calls,
                "not_searched": self._not_searched,
                "found": {label: self._found(label) for label in self._texts},
            }))

    def _found(self, label: str) -> dict:
        if label in self._in_first:
            return {"in": FIRST, **self._in_first[label]}
        found: dict = {"in": LATER, "request": self._later[label]} if label in self._later else {"in": None}
        why = self._why_not_searched()
        return found | ({"not_searched": why} if why else {})

    def _why_not_searched(self) -> str | None:
        """Why the first model call wasn't searched whole, or None if it was."""
        if self._first_cut:
            return f"past {self._first_cut['searched_bytes']} bytes of the first model call's {self._first_cut['bytes']}; its body was cut"
        if self._first_at is not None:
            return None
        if self._not_searched:
            requests = "1 compressed request wasn't" if self._not_searched == 1 else f"{self._not_searched} compressed requests weren't"
            return f"{NO_MODEL_CALL}; {requests} searched"
        return NO_MODEL_CALL


def _json_object(data: bytes) -> dict | None:
    """`data` as a JSON object, or None if it isn't one."""
    try:
        parsed = json.loads(data)
    except ValueError:  # not JSON, or not text: json.JSONDecodeError and UnicodeDecodeError are both ValueErrors
        return None
    return parsed if isinstance(parsed, dict) else None


def _heading(text: str, at: int) -> str | None:
    """The last line with any text before position `at` of `text` (the heading a harness put above a file), path folders as …/."""
    lines = [line.strip() for line in text[:at].split("\n") if line.strip()]
    return PATH_FOLDERS.sub("…/", lines[-1])[:HEADING_MAX] if lines else None


def _system(body: dict):
    """A request's system prompt: Anthropic's `system`, or the OpenAI format's system messages."""
    if "system" in body:
        return body["system"]
    return [message for message in body["messages"] if _is_system(message)]


def _not_system(body: dict) -> list:
    """A request's messages other than the OpenAI format's system messages."""
    return [message for message in body["messages"] if not _is_system(message)]


def _is_system(message) -> bool:
    return isinstance(message, dict) and message.get("role") == "system"


def requests_from_inspect_log(events: Iterable, attachments: Mapping[str, str]) -> tuple[list[bytes], int]:
    """The bodies Inspect sent the model for a Docker run's bridged calls, from its transcript's model events.

    Each is the event's `call.request` (docs/spike-findings.md, "Phase 5
    Task 31": in the log, with attachments resolved, it held the system
    prompt, messages and tools), with each string Inspect keeps aside as an
    `attachment://` reference put back. A reference it no longer holds is
    left as it is.

    Returns:
        (the bodies, in order; how many model events had no call, because
        Inspect logged none for them).
    """
    bodies, unlogged = [], 0
    for event in events:
        if getattr(event, "event", None) != "model":
            continue
        if event.call is None:
            unlogged += 1
            continue
        bodies.append(json.dumps(_resolved(event.call.request, attachments)).encode())
    return bodies, unlogged


def _resolved(value, attachments: Mapping[str, str]):
    """`value` with each string that is a whole `attachment://<id>` reference replaced by the text it refers to."""
    if isinstance(value, str) and value.startswith(ATTACHMENT):
        return attachments.get(value.removeprefix(ATTACHMENT), value)
    if isinstance(value, dict):
        return {name: _resolved(item, attachments) for name, item in value.items()}
    if isinstance(value, list):
        return [_resolved(item, attachments) for item in value]
    return value


def source_kind(source: str | None) -> str | None:
    """What kind of file an eval file's `source` is: "skill", "agent", "config" or "instructions"; None for no source.

    A config is a Kilo config, Claude Code's settings or any other JSON
    file, which no harness sends to the model as text: it enforces it.
    """
    if source is None:
        return None
    tested = under_test(source)
    if tested:
        return tested[0]
    if source in CONFIG_PATHS or source == CLAUDE_SETTINGS or source.endswith((".json", ".jsonc")):
        return "config"
    return "instructions"


def texts_to_find(target: str, source: str | None, staged: Mapping[str, str]) -> dict[str, str]:
    """What to look for in the requests of a run of the eval file whose source is `source`, label -> text.

    `staged` is what the template stages, so a baseline, which removes the
    source, is searched for the template's text and can say it wasn't sent.

    - An instruction file: its own text, labelled with its path. On
      claude-code AGENTS.md stands for every instruction file staged
      (project_files.claude_code_instructions) and each file they import,
      since Claude Code sends each under its own header; and each is looked
      for without its block-level HTML comments, which Claude Code strips
      before sending (project_files.without_block_comments). Kilo sends a
      file as it is, so on kilo nothing is removed.
    - A skill or an agent: its frontmatter `description` and its body, the
      text after the frontmatter, labelled "description" and "body", since
      the description is sent in every request and the body only once loaded.
    - A config file, a source that isn't staged, or no source: nothing.
    """
    kind = source_kind(source)
    if kind in ("skill", "agent"):
        text = staged.get(source)
        if text is None:
            return {}
        parts = {"description": frontmatter_value(text, "description"), "body": _body(text)}
        return {label: value for label, value in parts.items() if value}
    if kind != "instructions":
        return {}
    as_sent = without_block_comments if target == "claude-code" else str
    stands_for = claude_code_instructions(source, staged) if target == "claude-code" else []
    if stands_for:
        return {name: as_sent(staged[name]) for name in [*stands_for, *staged_imports(stands_for, staged)]}
    return {source: as_sent(staged[source])} if source in staged else {}


def _body(text: str) -> str:
    """A Markdown file's text after its frontmatter (the lines between a first `---` and the next), or all of it if it has none."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    ends = [index for index, line in enumerate(lines[1:], 1) if line.rstrip() == "---"]
    return "".join(lines[ends[0] + 1 :]) if ends else text


def thinking_sent(body: dict) -> str:
    """The thinking level a model request asked for, as the request put it, or NO_THINKING_SETTING."""
    fields = [f'"{name}": {json.dumps(body[name])}' for name in THINKING_FIELDS if name in body]
    return "; ".join(fields) or NO_THINKING_SETTING


def sent_level(sent: dict | None) -> str:
    """The thinking level the run's first model call carried, or why it can't be said.

    LEVEL_NOT_RECORDED for a run that read no requests, NO_MODEL_REQUEST if
    none of them was the run's model call, LEVEL_CUT if that call's body
    was over the cap. Never the level asked for: only a request says what
    reached the model.
    """
    if sent is None:
        return LEVEL_NOT_RECORDED
    if sent.get("first_body_cut"):
        return LEVEL_CUT
    if sent["first_body"] is None:
        return NO_MODEL_REQUEST
    return thinking_sent(sent["first_body"])


def thinking_text(events: str) -> str:
    """Whether a run's events hold the model's thinking text: TEXT_IN_EVENTS, TEXT_WITHHELD or NO_TEXT.

    Kilo writes it as `reasoning` events with the text in `part.text`, with
    `--thinking`; Claude Code as `thinking` blocks in an assistant message,
    whose `thinking` is empty unless it is told to show it
    (docs/spike-findings.md, "Phase 5 Task 31", "Extended thinking").
    """
    reasoning, blocks = [], []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "reasoning":
            reasoning.append((event.get("part") or {}).get("text"))
        content = (event.get("message") or {}).get("content") if event.get("type") == "assistant" else None
        if isinstance(content, list):
            blocks += [block.get("thinking") for block in content if isinstance(block, dict) and block.get("type") == "thinking"]
    if any(reasoning) or any(blocks):
        return TEXT_IN_EVENTS
    return TEXT_WITHHELD if blocks else NO_TEXT


@dataclass(frozen=True)
class Watch:
    """What a run records beyond the fields every run has: its requests, its thinking level, or both.

    Attributes:
        capture: True to keep the `sent` entry (`--capture-prompt`).
        texts: what to look for in the requests (texts_to_find), redacted.
        told: what Whitney told the harness about thinking (`--thinking`),
            as told_harness gives it, or None if nothing.
        model: the run's model, which the run's model call names (Capture).
    """

    capture: bool = False
    texts: Mapping[str, str] = field(default_factory=dict)
    told: str | None = None
    model: str | None = None


def watch_for(capture_prompt: bool, thinking: bool, texts: Mapping[str, str] | None, told: str, model: str | None = None) -> Watch | None:
    """The run's Watch, or None for a run with neither capture nor thinking, which records what every run records."""
    if not capture_prompt and not thinking:
        return None
    return Watch(capture_prompt, dict(texts or {}), told if thinking else None, model)


def told_harness(harness: str, docker: bool) -> str:
    """What `--thinking` tells the harness, as the run states it: `harness`'s own setting, and in Docker Inspect's too.

    In Docker the bridge drops the harness's own generation settings, so
    the eval's reasoning_tokens is what can apply (tasks.scenario_eval sets
    it to DOCKER_THINKING_TOKENS).
    """
    return f"{harness}; Inspect's reasoning_tokens {DOCKER_THINKING_TOKENS}" if docker else harness


def thinking_warnings(target: str, model: str, execution: str, capture_prompt: bool, thinking: bool) -> list[str]:
    """What `whitney run` warns about at the start of a run with `--thinking`: [] when nothing applies.

    `--thinking` is warned about, not refused (Task 31, open decision 21):
    - locally without `--capture-prompt`, where no request is read, so only
      what Whitney told the harness is recorded;
    - on the kilo target with a Kilo Gateway model (`kilo/...`) locally,
      where real runs on 2026-11-12 (Kilo 7.8.27, Haiku) showed the request
      still saying thinking is disabled (docs/results/2026-11-12-prompt-capture-runs.md).
    A Docker run reads Inspect's log whenever it is watched, so it records
    the level that reached the model either way.
    """
    if not thinking or execution != "local":
        return []
    warnings = []
    if not capture_prompt:
        warnings.append(
            "--thinking without --capture-prompt: the harness is told to think, but no request is read, so the level "
            "that reached the model won't be recorded; the log and report give only what Whitney told the harness"
        )
    if target == "kilo" and model.startswith("kilo/"):
        warnings.append(
            "--thinking on the kilo target through the Kilo Gateway (a kilo/ model): in real runs on 2026-11-12 (Kilo 7.8.27) "
            "the request still said thinking was disabled, so the model may not think; a provider key "
            "(--api-key-env with an anthropic/ model) did apply it"
        )
    return warnings


def run_entries(watch: Watch | None, sent: dict | None, events: str, *, docker: bool) -> dict:
    """The fields a watched run adds to its record: `sent` with capture on, and `thinking`; {} for a run not watched.

    Args:
        watch: the run's Watch, or None.
        sent: the requests read (a Capture's record), or None if none were.
            A Docker run reads Inspect's log whenever it is watched, so its
            thinking level comes from the request even with capture off.
        events: the run's event stream.
        docker: True for a Docker run.
    """
    if watch is None:
        return {}
    kept = {"sent": sent} if watch.capture else {}
    return {**kept, "thinking": thinking_record(watch.told, sent_level(sent), events, docker=docker)}


def score_entries(run: Mapping) -> dict:
    """What a run's score keeps of its `sent` and `thinking` entries, for the report: {} for a run without them.

    The score keeps how the requests were read, how many there were, how
    many weren't the run's model call and how many couldn't be read, and
    where each text was found (and, from Inspect's log, how many calls it
    didn't log), not the request bodies, which stay in the run's record.
    """
    kept: dict = {}
    sent, thinking = run.get("sent"), run.get("thinking")
    if isinstance(sent, dict):
        kept["sent"] = {
            "via": sent.get("via"), "requests": len(sent.get("requests") or []),
            "not_model_calls": sent.get("not_model_calls") or 0, "not_searched": sent.get("not_searched") or 0,
            "found": sent.get("found") or {},
            **({"unlogged": sent["unlogged"]} if "unlogged" in sent else {}),
        }
    if isinstance(thinking, dict):
        kept["thinking"] = thinking
    return kept


def from_inspect_log(events: Iterable, attachments: Mapping[str, str], texts: Mapping[str, str], redact: Callable[[str], str] = str) -> dict:
    """A Docker run's `sent` entry, from its transcript's model events (requests_from_inspect_log).

    It holds what a proxy's does, and `unlogged`: how many model events had
    no call, because Inspect logged none for them. No model is matched: the
    bridge answers every call the harness makes with the run's model, so
    each model event is a call to it, and the first is the first; a
    background call the harness made first can't be told apart here
    (docs/adding-tests.md).
    """
    bodies, unlogged = requests_from_inspect_log(events, attachments)
    found = Capture(VIA_INSPECT_LOG, texts, redact)
    for body in bodies:
        found.add(body)
    return {**found.record(), "unlogged": unlogged}


def thinking_record(set_by: str | None, level: str, events: str, *, docker: bool) -> dict:
    """A run's `thinking` entry: the level the run asked for, the level its first model call carried, and where the text is.

    A local run states `set`, what Whitney told the harness (NOT_SET if
    nothing), and `sent`, the level in the request it captured (sent_level;
    LEVEL_NOT_RECORDED without capture). A Docker run states `asked` and
    `applied` instead: the bridge drops the harness's own setting, so what
    was asked for and what reached the model can differ, and only the
    request says which level applied.

    Args:
        set_by: what Whitney told the harness, or None.
        level: sent_level of the run's capture.
        events: the run's event stream, for thinking_text.
        docker: True for a Docker run.
    """
    asked, reached = ("asked", "applied") if docker else ("set", "sent")
    return {asked: set_by or NOT_SET, reached: level, "text": thinking_text(events)}
