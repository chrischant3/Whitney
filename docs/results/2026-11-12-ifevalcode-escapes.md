# IFEvalCode check code lost its escapes in the sandbox, 2026-11-12

In a real IFEvalCode run (`python scripts/run_evals.py --target claude-code --model anthropic/claude-haiku-4-5 --evals ifevalcode --ifevalcode-samples-per-language 1 --epochs 1`), sample 505 failed correctness in both baseline and template with the same scorer error:

```
File "/app/solution.py", line 13
    ('<>&"'', 2),
SyntaxError: unterminated string literal
```

The row's `check_correctness` is valid Python, `('<>&\"\'', 2)`. A sample like this fails whatever the model writes, so it corrupts the baseline/template comparison.

## Where the escapes go

**Upstream, not Whitney.** Whitney passed `check_correctness` through unchanged. The scorer in inspect_evals 0.27.0 (`inspect_evals/ifevalcode/utils.py`) writes the file with `sandbox().write_file`, which is safe, but first its post-processors for Python, C++, Java and C# (`_post_process_python`, `_cpp`, `_java`, `_csharp`) call:

```python
def _decode_unicode_escapes(text: str) -> str:
    return text.encode("utf-8").decode("unicode_escape")
```

on the check code. JavaScript, TypeScript, PHP and shell are not decoded. That decode is there for rows the dataset over-escaped: some rows hold `\"` where `"` was meant (row 531: `test_case([\"INSERT a 10\", ...])`), which isn't valid as written. But it runs on every row, and on a row that is already valid it:

- turns `\"` and `\'` inside string literals into bare quotes (row 505, C# row 135);
- decodes UTF-8 bytes as Latin-1, so every non-ASCII character becomes mojibake (73 rows, most of them Chinese comments; in C# the mangled bytes can include U+0085, which C# reads as a line break);
- drops a backslash-newline line continuation (Python row 523);
- raises, erroring the sample, on an escape it can't decode such as a bare `\x`.

## The workaround, in Whitney

`whitney.interference.scorer_check_code` prepares the check code before it goes into the sample's metadata, for the four languages upstream decodes:

1. It decides whether the row is over-escaped. Python: it doesn't parse (`ast.parse`) as written and does once decoded. C++, Java, C#: it holds `\"` and no bare `"`, since those languages write strings only in double quotes, so a `\"` with no bare `"` has no string to sit in.
2. If so, it decodes the escapes itself, keeping non-ASCII text (`encode("latin-1", "backslashreplace").decode("unicode_escape")`).
3. It then doubles every backslash and writes non-ASCII characters as `\uXXXX`, so upstream's decode turns the text back into exactly that. Newlines stay as they are, because the C# post-processor finds `public class CheckCorrectness\n{\n` before it decodes.

The sample also records `check_correctness_as_written`, the row's own text, so a log reader can see both. Upstream is left at 0.27.0: the pin stays, and the workaround depends on its decode staying the same (the tests below run the real post-processors, so a change upstream fails them). It should be reported upstream; the text for that is at the end.

## Evidence

**Failing tests first.** `tests/test_ifevalcode_escapes.py` (new) runs the real upstream post-processors on what Whitney passes, starting with row 505's check code; `tests/test_interference.py::test_the_scorer_gets_check_code_its_decode_turns_back_into_the_row` checks the task wiring. With `scorer_check_code` returning the check code unchanged (Whitney's behaviour until now), 12 failed and 25 passed; the row 505 test failed on exactly the reported line, `('<>&"'', 2)`. With the fix, all pass, along with the tests for malformed escapes, a capitalised language name and an invalid escape that only warns, added afterwards.

**Every row, upstream alone against the fix.** `python spikes/ifevalcode_escape_survey.py dataset/ifevalcode_cleaned.jsonl`, on the cleaned dataset rebuilt from revision `7d2918680c806a235beed1224b2dc726a6771021` (the audit report came out unchanged):

| Language | Rows | Mojibake before → after | Lexical error before → after |
|---|---|---|---|
| Python | 105 | 10 → 0 | 3 (505, 540, 560) → 0 |
| C++ | 100 | 31 → 0 | 1 (23) → 0 |
| Java | 102 | 14 → 0 | 0 → 0 |
| C# | 103 | 18 → 0 | 3 (111, 132, 188) → 0 |

No row has a lexical error after the fix that it didn't have before. Python is checked by compiling it; C++ and Java by the g++ and javac errors only bad escapes cause; C# with `mcs` in the IFEvalCode image (`--network none`), counting CS1009, CS1010, CS1011, CS1012, CS1015, CS1035, CS1039 and CS1056, since `mcs` isn't installed on the host. Semantic errors are expected throughout: the check code is compiled without a solution.

**Row 505 in the real sandbox.** `python spikes/ifevalcode_505_sandbox.py dataset/ifevalcode_cleaned.jsonl <log-dir> <compose.yaml>` runs the upstream scorer in the IFEvalCode Docker image on row 505 with one fixed answer, no model called:

- before: `File "/app/solution.py", line 13 ('<>&"'', 2), SyntaxError: unterminated string literal (detected at line 13)`, the reported error;
- after: the file compiles and the row's asserts run. The fixed answer fails one, `Failed for input <<>>&&&""''', got 2, expected 4`. Row 505's expected values look inconsistent with each other (`'<>&"\''` expects 2, `<<>>&&&""'''` expects 4), which is a separate question about the row, not checked further.

The image was built from upstream's Dockerfile with the session's proxy CA certificate added, because the sandbox it ran in intercepts TLS and pip failed to verify PyPI without it. The compose file used was upstream's with `image:` naming that build and `build:` removed; `network_mode: none` was kept.

## The rerun with a model

The reported command, rerun with the fix: `scripts/run_evals.py`'s own `_parse` and `_tasks` building the tasks from `--target claude-code --model anthropic/claude-haiku-4-5 --evals ifevalcode --ifevalcode-samples-per-language 1 --epochs 1`, and the same `eval(task, model=None, epochs=1, ...)` call, with one addition: Inspect's `sandbox=` override naming the compose file above, since upstream's image won't build behind this session's proxy. Claude Code 2.1.322 called Haiku with a real key, one sample per variant, a few cents in all.

| Variant | Correctness | Instruction | Correctness explanation |
|---|---|---|---|
| baseline | 0 | 0 | `/app/solution.py`, line 37: `AssertionError: Failed for input <<>>&&&""''', got 2, expected 4` |
| template | 0 | 0 | line 36: the same assertion |

Sample 505's correctness error is no longer a SyntaxError: the check code compiles and its asserts run. Haiku's answer fails the same case as the fixed answer above, which adds weight to the row's expectations being inconsistent. Both variants also failed the instruction check, each with `No ternary operator found`; one sample, one epoch, says nothing about the template either way. The logs were kept in a scratch folder and are not committed.

## To report upstream (UKGovernmentBEIS/inspect_evals)

> **IFEvalCode: `_decode_unicode_escapes` corrupts valid check code**
>
> In inspect_evals 0.27.0, `ifevalcode/utils.py` runs every Python, C++, Java and C# row's `check_correctness` through `text.encode("utf-8").decode("unicode_escape")`. That repairs rows that hold `\"` where `"` was meant, but breaks rows that are already valid: row 505's `('<>&\"\'', 2)` becomes `('<>&"'', 2)`, a SyntaxError, so the sample fails correctness whatever the model writes. On revision `7d29186` of the dataset, 7 rows become lexically invalid this way (Python 505, 540, 560; C++ 23; C# 111, 132, 188), 73 rows' non-ASCII text becomes mojibake (UTF-8 decoded as Latin-1), backslash-newline continuations are dropped, and a malformed escape raises and errors the sample. Suggested fix: decode only rows that are over-escaped (for Python, rows that don't compile as written and do once decoded), and decode with `encode("latin-1", "backslashreplace").decode("unicode_escape")` so non-ASCII text survives.
