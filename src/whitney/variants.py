"""Variants: the changes to the staged files that each variant beside `template` runs with.

An eval file declares its variants (whitney.evalfile.Variant), or gets the
default baseline, which removes its `source`:

    "variants": {
      "baseline":      {"role": "baseline", "remove": ["AGENTS.md"]},
      "allow_control": {"role": "control",  "set": {"kilo.jsonc": {"/permission/external_directory": "allow"}}}
    }

`remove` leaves staged paths out; a path ending in "/" leaves out everything
under it. `set` puts values at JSON Pointers (RFC 6901) in a staged JSON or
JSONC file, which is then rewritten as `json.dumps(config, indent=2)`, with
no trailing newline, as Kilo configs have always been written for a control.

Only the part of JSON Pointer that setting a value needs is here: the
`~0` and `~1` escapes, and objects only. A pointer through an array, a
string or a missing key is refused rather than guessed at.

Problems are (location, message) pairs, the location relative to the
variant, such as ("remove", 1) or ("set", "kilo.jsonc", "/permission/bash"),
so whitney.validate can name the place in the eval file.
"""

import json
import re
from collections.abc import Mapping
from typing import Any

from whitney.evalfile import EvalFile, V1File, Variant, location, version_1_variants
from whitney.project_files import CONFIG_PATHS
from whitney.static_checks import parse_jsonc

# The staged files as supplied. Every eval file runs it, so no declared variant may take the name.
TEMPLATE = "template"
SKILL_SOURCE = re.compile(r"^(\.kilo/skills/[^/]+/)SKILL\.md$")
BAD_ESCAPE = re.compile(r"~(?![01])")

Located = tuple[tuple, str]  # (location within the variant, message)


class PointerError(ValueError):
    """A JSON Pointer that is badly written, or that can't be set in the document it points into."""


class VariantError(ValueError):
    """A variant can't be applied to the staged files.

    Attributes:
        problems: (location within the variant, message) for each part that can't.
    """

    def __init__(self, problems: list[Located]) -> None:
        """Args:
        problems: (location, message) pairs, at least one.
        """
        self.problems = problems
        super().__init__("; ".join(f"{location(at)}: {message}" for at, message in problems))


def default_baseline(source: str) -> Variant:
    """The baseline an eval file gets when it leaves out `variants`: `source` removed.

    For a Kilo skill, `.kilo/skills/<name>/SKILL.md`, the skill's whole folder
    is removed, because files beside SKILL.md belong to the skill too.
    """
    skill = SKILL_SOURCE.fullmatch(source)
    return Variant(role="baseline", remove=[skill.group(1) if skill else source])


def declared_variants(eval_file: EvalFile | V1File) -> dict[str, Variant]:
    """The variants an eval file runs besides `template`: its own, or the default baseline if it leaves them out.

    `"variants": {}` gives none, so only `template` runs. A version 1 file as
    read (not upgraded) gets the variants the upgrade would give it, so a
    file the upgrade refuses, such as one with no token keys, still has them.
    """
    if isinstance(eval_file, V1File):
        upgraded = version_1_variants(eval_file)
        declared = None if upgraded is None else {name: Variant.model_validate(v) for name, v in upgraded.items()}
    else:
        declared = eval_file.variants
    if declared is None:
        return {"baseline": default_baseline(eval_file.source)}
    return dict(declared)


def pointer_tokens(pointer: str) -> list[str]:
    """The keys a JSON Pointer names, unescaped: "/a~1b/c~0d" is ["a/b", "c~d"].

    Raises:
        PointerError: it doesn't start with "/", or a "~" isn't followed by 0 or 1.
    """
    if not pointer.startswith("/"):
        raise PointerError(f"{pointer!r} must start with /")
    if BAD_ESCAPE.search(pointer):
        raise PointerError(f"{pointer!r}: ~ must be followed by 0 or 1 (~0 is ~, ~1 is /)")
    # ~1 before ~0, as RFC 6901 says, so "~01" is the key "~1" and not "/".
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def pointer_from(tokens: list[str]) -> str:
    """The JSON Pointer for these keys, escaped: the inverse of pointer_tokens."""
    return "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in tokens)


def apply_variant(files: Mapping[str, str], variant: Variant) -> dict[str, str]:
    """The staged files as this variant runs them: `remove` first, then `set`.

    Args:
        files: the staged files, project-relative path to text. Not changed.
        variant: the changes to make.

    Returns:
        A new dict of the files after the changes.

    Raises:
        VariantError: a `remove` path or `set` file isn't staged, a `set`
            file isn't JSON or JSONC or is also removed, a pointer can't be
            set, or a value can't be written as JSON (NaN, Infinity). Every
            problem is listed, not only the first. A path two `remove`
            entries both cover is removed once (see repeated_removes).
    """
    problems: list[Located] = []
    updated = dict(files)
    for index, path in enumerate(variant.remove):
        matched = staged_matches(files, path)
        if not matched:
            problems.append((("remove", index), f"'{path}' isn't staged, so removing it changes nothing; {_listing(files)}"))
        for match in matched:
            # An earlier entry may have removed it already; repeated_removes reports that, here it's harmless.
            updated.pop(match, None)
    for path, pointers in variant.set_pointers.items():
        if path not in files:
            problems.append((("set", path), f"'{path}' isn't staged, so set can't change it; {_listing(files)}"))
        elif path not in updated:
            problems.append((("set", path), f"'{path}' is also removed by this variant, so set can't change it"))
        else:
            try:
                document = parse_jsonc(updated[path])
            except ValueError as e:
                problems.append((("set", path), f"'{path}' isn't JSON or JSONC ({e}), so set can't change it"))
                continue
            for pointer, value in pointers.items():
                try:
                    _put(document, pointer, value)
                except PointerError as e:
                    problems.append((("set", path, pointer), str(e)))
            try:
                # allow_nan=False: NaN and Infinity aren't JSON, and Kilo would refuse the file.
                updated[path] = json.dumps(document, indent=2, allow_nan=False)
            except ValueError as e:
                problems.append((("set", path), (
                    f"'{path}' can't be written as JSON after set ({e}); set values must be JSON: no NaN or Infinity"
                )))
    if problems:
        raise VariantError(problems)
    return updated


def changes_nothing(files: Mapping[str, str], changed: Mapping[str, str]) -> bool:
    """True if `changed` (what apply_variant returned for `files`) stages what `files` does.

    A file `set` rewrote counts as unchanged if it parses to the same value:
    dropping its comments or reformatting it changes nothing Kilo reads.
    """
    if changed.keys() != files.keys():
        return False
    return all(
        changed[path] == text or _canonical(parse_jsonc(changed[path])) == _canonical(parse_jsonc(text))
        for path, text in files.items()
    )


def _canonical(value: Any) -> str:
    """`value` as JSON with sorted keys, so true and 1, or 1.0 and 1, differ as they do to Kilo, and key order doesn't."""
    return json.dumps(value, sort_keys=True)


def shadowed_sets(files: Mapping[str, str], variant: Variant) -> list[Located]:
    """Each `set` on a Kilo config that another staged Kilo config could shadow.

    Kilo reads more than one config from a project (kilo.jsonc and
    .kilo/kilo.jsonc among them), so a value the variant sets in one could be
    overridden by another. A pointer is shadowed if, after the variant, another
    Kilo config holds a different value there, or holds something that isn't
    an object on the way to it (`"permission": "ask"` covers every permission).
    That needs no knowledge of which file wins. It does assume a later file
    replaces a value whole; if Kilo merged objects key by key, a value that
    differs only in keys the other file doesn't set would be reported too.
    A config that can't be parsed,
    or isn't an object, can't be read by Kilo either, so it shadows nothing.

    Raises:
        VariantError: as apply_variant.
    """
    changed = apply_variant(files, variant)
    others = {}
    for path in CONFIG_PATHS:
        try:
            others[path] = parse_jsonc(changed[path])
        except (KeyError, ValueError):
            continue
        if not isinstance(others[path], dict):
            del others[path]
    found = []
    for path, pointers in variant.set_pointers.items():
        if path not in CONFIG_PATHS:
            continue
        for pointer, value in pointers.items():
            tokens = pointer_tokens(pointer)
            for other, document in others.items():
                held = None if other == path else _holding(document, tokens, value)
                if held is not None:
                    found.append((("set", path, pointer), (
                        f"'{other}' also sets {held}, and Kilo reads both files, so it could shadow this change; "
                        f"set the same value there too, or remove '{other}' in this variant"
                    )))
    return found


def _holding(document: Any, tokens: list[str], value: Any) -> str | None:
    """The pointer at which `document` holds something other than `value` on the way to `tokens`, or None."""
    for depth, token in enumerate(tokens):
        if not isinstance(document, dict):
            return pointer_from(tokens[:depth])
        if token not in document:
            return None
        document = document[token]
    return None if _canonical(document) == _canonical(value) else pointer_from(tokens)


def _put(document: Any, pointer: str, value: Any) -> None:
    """Put `value` at `pointer` in `document`, changing it in place.

    Every key on the way must exist and hold an object; only the last may be new.

    Raises:
        PointerError: the pointer is badly written, or a key on the way is
            missing or isn't an object.
    """
    tokens = pointer_tokens(pointer)
    target = document
    for depth, token in enumerate(tokens):
        if not isinstance(target, dict):
            where = pointer_from(tokens[:depth]) or "the file"
            raise PointerError(f"{where} isn't an object, so set can't put {pointer} inside it")
        if depth == len(tokens) - 1:
            target[token] = value
        elif token not in target:
            parent = pointer_from(tokens[: depth + 1])
            raise PointerError(f"{parent} doesn't exist, so set can't put {pointer} inside it; set {parent} to an object instead")
        else:
            target = target[token]


def repeated_removes(variant: Variant) -> list[Located]:
    """Each `remove` entry that an earlier one already covers, or that covers an earlier one.

    apply_variant removes such a path once, so the variant still runs; this
    is for validation, because a repeated entry is usually a mistake.
    """
    found = []
    for index, path in enumerate(variant.remove):
        for earlier, before in enumerate(variant.remove[:index]):
            if _covers(before, path):
                found.append((("remove", index), f"'{path}' is already removed by remove[{earlier}] ('{before}'); list each path once"))
                break
            if _covers(path, before):
                found.append((("remove", index), f"'{path}' also removes remove[{earlier}] ('{before}'); list each path once"))
                break
    return found


def _covers(entry: str, path: str) -> bool:
    return entry == path or (entry.endswith("/") and path.startswith(entry))


def spread_over_kilo_configs(variant: Variant, files: Mapping[str, str], source: str) -> Variant:
    """`variant` with the pointers it sets in `source` also set in every other staged Kilo config it doesn't name.

    Kilo reads every config in a project, so the kilo_jsonc wrapper's control
    on kilo.jsonc (allow_control) is put in each one, as
    set_external_directory always did: a project with only .kilo/kilo.jsonc,
    or both, gets it in each. The wrapper and validation both use this, for
    the shipped kilo-jsonc.json and any version 1 file, so validation checks
    what actually runs.

    Entries for other files, and for Kilo configs the variant names itself,
    are kept as declared. `source` is dropped only if it isn't staged and
    another config takes the pointers. A missing parent object is created,
    as today's setdefault did, by moving the pointer up to the first missing
    key (see _with_missing_parents).

    Returns:
        `variant` itself if `source` isn't a Kilo config, sets nothing, or
        no config it could go in is staged.
    """
    pointers = variant.set_pointers.get(source)
    targets = [path for path in CONFIG_PATHS if path in files and (path == source or path not in variant.set_pointers)]
    if source not in CONFIG_PATHS or not pointers or not targets:
        return variant
    spread = {path: sets for path, sets in variant.set_pointers.items() if path != source}
    for path in targets:
        try:
            spread[path] = _with_missing_parents(parse_jsonc(files[path]), pointers)
        except ValueError:
            spread[path] = dict(pointers)  # apply_variant names the file that isn't JSON
    return variant.model_copy(update={"set_pointers": spread})


def _with_missing_parents(document: Any, pointers: Mapping[str, Any]) -> dict[str, Any]:
    """`pointers` rewritten so that setting them in `document` creates any missing parent object.

    A pointer whose parent is missing moves up to the first missing key,
    with the rest nested in its value: /permission/bash on {} becomes
    /permission = {"bash": ...}. Pointers that move to the same key are
    merged, so siblings under one missing parent all survive. A parent that
    isn't an object is left as it is, for apply_variant to refuse.
    """
    out: dict[str, Any] = {}
    for pointer, value in pointers.items():
        moved, nested = _moved_up(document, pointer, value)
        out[moved] = _merged(out[moved], nested) if moved in out and moved != pointer else nested
    return out


def _moved_up(document: Any, pointer: str, value: Any) -> tuple[str, Any]:
    tokens = pointer_tokens(pointer)
    for depth, token in enumerate(tokens[:-1]):
        if not isinstance(document, dict):
            break
        if token not in document:
            for key in reversed(tokens[depth + 1 :]):
                value = {key: value}
            return pointer_from(tokens[: depth + 1]), value
        document = document[token]
    return pointer, value


def _merged(into: Any, extra: Any) -> Any:
    """`extra` laid over `into`, object by object, as setting its pointers one after another would leave it."""
    if not (isinstance(into, dict) and isinstance(extra, dict)):
        return extra
    merged = dict(into)
    for key, value in extra.items():
        merged[key] = _merged(merged[key], value) if key in merged else value
    return merged


def staged_matches(files: Mapping[str, str], path: str) -> list[str]:
    """The staged paths a `remove` entry leaves out: `path` itself, or everything under it if it ends in "/"."""
    if path.endswith("/"):
        return [staged for staged in files if staged.startswith(path)]
    return [path] if path in files else []


def _listing(files: Mapping[str, str]) -> str:
    return f"staged files: {', '.join(sorted(files)) or 'none'}"
