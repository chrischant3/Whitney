"""What upstream's unicode_escape decode does to every row's check code, with and without Whitney's fix.

For each row in a language inspect_evals 0.27.0 decodes (python, cpp, java,
csharp), compares the check code the sandbox gets from upstream alone with
what it gets through `whitney.interference.scorer_check_code`. It counts rows
whose non-ASCII text turns into mojibake and rows with a lexical error: Python
by compiling, C++ and Java by g++ and javac errors that only bad escapes cause
(semantic errors are expected, since there's no solution). C# is counted for
mojibake only, as mcs isn't installed here.

Run (results in docs/results/2026-11-12-ifevalcode-escapes.md):
    python spikes/ifevalcode_escape_survey.py dataset/ifevalcode_cleaned.jsonl
"""

import collections
import json
import re
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

from inspect_evals.ifevalcode.utils import _decode_unicode_escapes as upstream_decode

from whitney.interference import UPSTREAM_DECODES, scorer_check_code

LEXICAL = {
    "cpp": re.compile(r"stray '\\\\'|missing terminating|unterminated|empty character constant"),
    "java": re.compile(r"illegal character: '\\\\'|unclosed (string|character) literal|empty character literal|illegal line end"),
}


def lexical_error(language: str, code: str) -> bool:
    if language == "python":
        try:
            compile(code, "check_correctness", "exec")
        except SyntaxError:
            return True
        return False
    with tempfile.TemporaryDirectory() as folder:
        if language == "cpp":
            source = Path(folder, "check.cpp")
            source.write_text(code, encoding="utf-8")
            command = ["g++", "-fsyntax-only", "-fmax-errors=0", str(source)]
        else:
            source = Path(folder, "TestCases.java")
            source.write_text(code, encoding="utf-8")
            command = ["javac", "-Xmaxerrs", "1000", "-d", folder, str(source)]
        return bool(LEXICAL[language].search(subprocess.run(command, capture_output=True, text=True).stderr))


def mojibake(original: str, sent: str) -> bool:
    return any(ord(char) > 127 and char not in sent for char in original)


def main(path: str) -> None:
    warnings.simplefilter("ignore")
    tally: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    broken: dict[str, list] = collections.defaultdict(list)
    worse = []
    for row in map(json.loads, open(path, encoding="utf-8")):
        language = row["programming_language"].lower()
        if language not in UPSTREAM_DECODES:
            continue
        check = row["check_correctness"]
        before = upstream_decode(check)
        after = upstream_decode(scorer_check_code(language, check))
        counts = tally[language]
        counts["rows"] += 1
        counts["mojibake_before"] += mojibake(check, before)
        counts["mojibake_after"] += mojibake(check, after)
        if language == "csharp":
            continue
        error_before, error_after = lexical_error(language, before), lexical_error(language, after)
        counts["lexical_error_before"] += error_before
        counts["lexical_error_after"] += error_after
        if error_before:
            broken[language].append(row["id"])
        if error_after and not error_before:
            worse.append((language, row["id"]))
    for language, counts in sorted(tally.items()):
        print(language, dict(counts), "broken before:", broken[language])
    print("rows worse after the fix:", worse)


if __name__ == "__main__":
    main(sys.argv[1])
