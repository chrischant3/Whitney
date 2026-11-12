"""Check code reaches the sandbox as the dataset wrote it.

inspect_evals 0.27.0's IFEvalCode post-processors for Python, C++, Java and C#
run every row's check code through `unicode_escape` before writing it to the
sandbox. That repairs the rows the dataset over-escaped (`\\"` where `"` was
meant) but breaks every row that is already valid: row 505's `('<>&\\"\\'', 2)`
became `('<>&"'', 2)`, a SyntaxError, so correctness failed whatever the
model wrote. It also turns every non-ASCII character into mojibake and drops a
backslash-newline continuation. These tests run the real post-processors.
"""

import warnings

import pytest
from inspect_evals.ifevalcode.utils import POST_PROCESSORS

from whitney.interference import scorer_check_code

# Row 505's check code, first lines, verbatim: valid Python whose strings hold \" and \'.
ROW_505 = r"""def check_correctness():
    test_cases = [
        ('Hello <World> & \"Everyone\"', 5),
        ('SafeString', 0),
        ('<>&\"\'', 2),
    ]
    for text, expected in test_cases:
        assert count_special(text) == expected
"""
ROW_505_SOLUTION = "def count_special(text):\n    return 0\n"

# Row 531's shape: over-escaped, \" stands where " was meant, so it is not valid Python as written.
OVER_ESCAPED_PYTHON = r"""def check_correctness():
    assert total([\"INSERT a 10\", \"INSERT b 20\"]) == 30
"""


def _sent_to_sandbox(language, solution, check_code):
    """What the upstream post-processor writes to the sandbox file, given Whitney's check code."""
    return POST_PROCESSORS[language](solution, scorer_check_code(language, check_code))


def test_row_505_reaches_the_sandbox_with_its_escapes_and_compiles():
    full = _sent_to_sandbox("python", ROW_505_SOLUTION, ROW_505)

    assert ROW_505 in full
    compile(full, "/app/solution.py", "exec")  # the reported SyntaxError, if the escapes were lost


def test_an_over_escaped_python_row_is_still_repaired():
    full = _sent_to_sandbox("python", "def total(xs):\n    return 30\n", OVER_ESCAPED_PYTHON)

    assert 'total(["INSERT a 10", "INSERT b 20"])' in full
    compile(full, "solution.py", "exec")


def test_a_python_line_continuation_survives():
    check = "def check_correctness():\n    assert 1 == \\\n        1\n"

    assert check in _sent_to_sandbox("python", "", check)


@pytest.mark.parametrize("language", ["python", "cpp", "java", "csharp"])
def test_non_ascii_text_survives(language):
    check = "// 测试用例1：基本测试 café\n" if language != "python" else "# 测试用例1：基本测试 café\n"

    assert check in _sent_to_sandbox(language, "", check)


def test_an_over_escaped_row_keeps_its_non_ascii_text_when_repaired():
    check = "def check_correctness():\n    # 测试\n    assert f(\\\"é\\\") == 1\n"

    assert 'assert f("é") == 1' in _sent_to_sandbox("python", "", check)
    assert "# 测试" in _sent_to_sandbox("python", "", check)


@pytest.mark.parametrize("language", ["cpp", "java", "csharp"])
def test_a_c_family_string_holding_escaped_quotes_is_kept(language):
    # C# row 135's shape: real string literals, with \" inside them.
    check = 'var html = "<iframe width=\\"560\\"></iframe>";\nvar empty = "";\n'

    assert check in _sent_to_sandbox(language, "", check)


@pytest.mark.parametrize("language", ["cpp", "java", "csharp"])
def test_an_over_escaped_c_family_row_is_still_repaired(language):
    # C++ row 0's shape: no bare " anywhere, \" standing where " was meant.
    check = 'assert(convert({1, 2}) == \\"1_2\\");\n'

    assert 'assert(convert({1, 2}) == "1_2");' in _sent_to_sandbox(language, "", check)


def test_the_csharp_entry_point_is_still_inserted():
    # Upstream adds Main by matching this header before it decodes, so the header must stay as written.
    check = 'public class CheckCorrectness\n{\n    public static bool check_correctness() { return f("\\"") == 1; }\n}\n'

    full = _sent_to_sandbox("csharp", "", check)

    assert "public static void Main(string[] args)" in full
    assert 'f("\\"")' in full


@pytest.mark.parametrize("language", ["javascript", "typescript", "php", "shell"])
def test_languages_upstream_does_not_decode_are_left_alone(language):
    check = 'echo "a\\"b" # 测试\n'

    assert scorer_check_code(language, check) == check


def test_an_empty_check_is_left_empty():
    assert scorer_check_code("python", "") == ""


@pytest.mark.parametrize(
    "language, check",
    [
        ("python", 'def check_correctness():\n    assert f(\\"\\N\\") == 1\n'),  # \N with no name can't be decoded
        ("cpp", 'assert(f(\\"\\x\\") == 1);\n'),  # nor can \x with no digits
    ],
)
def test_a_row_whose_escapes_cannot_be_decoded_reaches_the_sandbox_as_written(language, check):
    # Upstream's own decode would raise here and error the sample instead of scoring it.
    assert check in _sent_to_sandbox(language, "", check)


def test_the_language_name_is_matched_whatever_its_case():
    assert scorer_check_code("Python", ROW_505) == scorer_check_code("python", ROW_505) != ROW_505


def test_valid_python_holding_an_invalid_escape_is_kept_as_written():
    # "\d" only warns in Python 3.12 and later; a warning raised as an error must not make the row look broken.
    check = 'import re\ndef check_correctness():\n    assert re.match("\\d", \'1\\"\')\n'

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        full = _sent_to_sandbox("python", "", check)

    assert check in full
