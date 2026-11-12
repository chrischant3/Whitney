"""What a run sent to the model, and the thinking level it set (Phase 5 Task 31).

A run with capture turned on (`whitney run --capture-prompt`) keeps a
`sent` entry in its record under agent.RUN_STORE_KEY: the first model
request's body in full, then each request's method, path and size, and for
each later model request whether its system prompt and tools differ from the
first's. It also says which of the texts it was given to look for (the eval
file's `source`, see texts_to_find) each request held. Header values are
never seen here: the proxy (capture_proxy) hands over the body only. A local
run reads the requests through that proxy; a Docker run reads Inspect's own
log of the bridged calls (requests_from_inspect_log). The agents redact the
whole record (agent.redact_run) before it is stored.

A run with capture or `--thinking` keeps a `thinking` entry too: the
thinking level the run set, as the harness was told it, and what the
request held, never a bare on or off (thinking_record). The thinking text
stays where each harness writes it, in the run's events.

Evidence for every field read here: docs/spike-findings.md, "Phase 5 Task
31", and the requests in tests/data/prompt-capture/.
"""

import json
import threading
from collections.abc import Iterable, Mapping

from whitney.evalfile import under_test
from whitney.project_files import CLAUDE_SETTINGS, CONFIG_PATHS, claude_code_instructions, staged_imports
from whitney.validate import frontmatter_value

VIA_PROXY = "proxy"
VIA_INSPECT_LOG = "inspect-log"
NOT_SET = "not set by Whitney"
NOT_CAPTURED = "not captured"
NO_MODEL_REQUEST = "no model request was captured"
NO_THINKING_SETTING = "no thinking setting in the request"
# The request fields that carry a thinking level: Anthropic's `thinking`, and the OpenAI format's `reasoning`
# that Kilo sent through the wrong Gateway address (docs/results/2026-11-12-capture-followups.md).
THINKING_FIELDS = ("thinking", "reasoning", "reasoning_effort")
TEXT_IN_EVENTS = "in the events"
TEXT_WITHHELD = "withheld: the thinking blocks are empty"
NO_TEXT = "none in the events"
ATTACHMENT = "attachment://"  # how Inspect's transcript refers to a long string it keeps aside
SKILL_PARTS = ("description", "body")


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


class Capture:
    """A run's `sent` entry, built one request at a time; safe to add to from several threads.

    Only the first model request's body is kept. A model request is a JSON
    object with a `messages` list, the shape both Anthropic's and the OpenAI
    format's requests have; anything else (Kilo's model list, say) is listed
    by its method, path and size only, and never searched.

    Args:
        via: VIA_PROXY or VIA_INSPECT_LOG, where the requests came from.
        texts: label -> text to look for in every model request, after
            normalising both (texts_to_find). An empty text is left out.
    """

    def __init__(self, via: str, texts: Mapping[str, str]) -> None:
        self._via = via
        self._texts = {label: normalised(text) for label, text in texts.items() if normalised(text)}
        self._lock = threading.Lock()
        self._requests: list[dict] = []
        self._first: dict | None = None
        self._first_at: int | None = None
        self._found: dict[str, int | None] = dict.fromkeys(self._texts)

    def add(self, body: bytes, method: str | None = None, path: str | None = None) -> None:
        """Record one request: its method and path (None from Inspect's log) and its raw body."""
        parsed = _model_request(body)
        entry: dict = {"method": method, "path": path, "bytes": len(body), "model_request": parsed is not None}
        with self._lock:
            index = len(self._requests)
            if parsed is not None and self._first is None:
                self._first, self._first_at = parsed, index
            elif parsed is not None:
                entry["system_differs"] = _system(parsed) != _system(self._first)
                entry["tools_differ"] = parsed.get("tools") != self._first.get("tools")
            if parsed is not None:
                text = normalised(request_text(parsed))
                for label, wanted in self._texts.items():
                    if self._found[label] is None and wanted in text:
                        self._found[label] = index
            self._requests.append(entry)

    def record(self) -> dict:
        """The `sent` entry as it stands: via, requests, first_request (an index or None), first_body and found."""
        with self._lock:
            return json.loads(json.dumps({
                "via": self._via,
                "requests": self._requests,
                "first_request": self._first_at,
                "first_body": self._first,
                "found": self._found,
            }))


def _model_request(body: bytes) -> dict | None:
    """The body as a JSON object, if it is a model request (it has a `messages` list), else None."""
    try:
        parsed = json.loads(body)
    except ValueError:  # not JSON, or not text: json.JSONDecodeError and UnicodeDecodeError are both ValueErrors
        return None
    return parsed if isinstance(parsed, dict) and isinstance(parsed.get("messages"), list) else None


def _system(body: dict):
    """A request's system prompt: Anthropic's `system`, or the OpenAI format's system messages."""
    if "system" in body:
        return body["system"]
    return [message for message in body["messages"] if isinstance(message, dict) and message.get("role") == "system"]


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
      since Claude Code sends each under its own header.
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
    stands_for = claude_code_instructions(source, staged) if target == "claude-code" else []
    if stands_for:
        return {name: staged[name] for name in [*stands_for, *staged_imports(stands_for, staged)]}
    return {source: staged[source]} if source in staged else {}


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
    """The thinking level in a `sent` entry's first model request: NOT_CAPTURED without one, NO_MODEL_REQUEST if it has none."""
    if sent is None:
        return NOT_CAPTURED
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


def thinking_record(set_by: str | None, level: str, events: str, *, docker: bool) -> dict:
    """A run's `thinking` entry: the level the run set, what its first model request held, and where the text is.

    A local run states `set`, what Whitney told the harness (NOT_SET if
    nothing), and `sent`, the level in the request it captured (sent_level).
    A Docker run states `asked` and `applied` instead: the bridge drops the
    harness's own setting, so what was asked for and what reached the model
    can differ, and only the request says which level applied.

    Args:
        set_by: what Whitney told the harness, or None.
        level: sent_level of the run's capture.
        events: the run's event stream, for thinking_text.
        docker: True for a Docker run.
    """
    asked, reached = ("asked", "applied") if docker else ("set", "sent")
    return {asked: set_by or NOT_SET, reached: level, "text": thinking_text(events)}
