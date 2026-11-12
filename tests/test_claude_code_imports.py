"""The claude-code loader stages the files its instruction files import with @path.

Claude Code expands `@path` imports in CLAUDE.md, .claude/CLAUDE.md and
AGENTS.md (its memory docs, https://code.claude.com/docs/en/memory.md, read
2026-11-12), and the Task 31 follow-up spike showed what it sent
(docs/spike-findings.md, "Phase 5 Task 31 follow-up: CLAUDE.md imports"). A
staged project without the imported files would give the model less than the
real project does.
"""

import pytest

from whitney.project_files import (
    ENV_IMPORT_REASON,
    IMPORT_MAX_HOPS,
    LOCAL_REASON,
    OUTSIDE_IMPORT_REASON,
    files_not_staged,
    files_not_staged_reasons,
    load_claude_code_project_files,
    not_staged_reason,
)


def _project(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            (root / name).write_bytes(text)
        else:
            (root / name).write_text(text)
    return root


def test_a_claude_md_that_only_imports_agents_md_stages_agents_md_too(tmp_path):
    _project(tmp_path, {"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": "agents rule\n"})

    assert load_claude_code_project_files(tmp_path) == {"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": "agents rule\n"}
    assert files_not_staged("claude-code", tmp_path) == []


def test_a_claude_md_with_its_own_text_and_imports_stages_each_import(tmp_path):
    _project(tmp_path, {
        "CLAUDE.md": "# Rules\n\n- Own rule.\n- Shared: @AGENTS.md\n- See @docs/rules.md for the team's.\n",
        "AGENTS.md": "agents rule\n",
        "docs/rules.md": "team rule\n",
        "docs/other.md": "not imported\n",
    })

    assert sorted(load_claude_code_project_files(tmp_path)) == ["AGENTS.md", "CLAUDE.md", "docs/rules.md"]


def test_an_import_is_relative_to_the_file_holding_it_and_imports_are_followed(tmp_path):
    _project(tmp_path, {
        ".claude/CLAUDE.md": "@../docs/a.md\n",
        "docs/a.md": "a @b.md\n",
        "docs/b.md": "b\n",
        "b.md": "the wrong b\n",
    })

    staged = load_claude_code_project_files(tmp_path)

    assert staged == {".claude/CLAUDE.md": "@../docs/a.md\n", "docs/a.md": "a @b.md\n", "docs/b.md": "b\n"}


def test_imports_are_followed_four_hops_and_no_further(tmp_path):
    # The docs give a maximum depth of four hops; the spike's real run sent hops 1 to 4 and not 5.
    chain = {"CLAUDE.md": "@h1.md\n", **{f"h{n}.md": f"hop {n} @h{n + 1}.md\n" for n in range(1, 7)}}
    _project(tmp_path, chain)

    staged = load_claude_code_project_files(tmp_path)

    assert IMPORT_MAX_HOPS == 4
    assert sorted(staged) == ["CLAUDE.md", "h1.md", "h2.md", "h3.md", "h4.md"]


def test_imports_in_code_spans_and_fenced_blocks_are_not_followed(tmp_path):
    _project(tmp_path, {
        "CLAUDE.md": "Write `@span.md` to mention it.\n```\n@fenced.md\n```\n~~~\n@tilde.md\n~~~\nAfter the fences: @after.md\n",
        "span.md": "s\n", "fenced.md": "f\n", "tilde.md": "t\n", "after.md": "a\n",
    })

    assert sorted(load_claude_code_project_files(tmp_path)) == ["CLAUDE.md", "after.md"]


def test_an_escaped_space_is_part_of_the_path_and_a_quoted_path_or_an_email_is_not_an_import(tmp_path):
    _project(tmp_path, {
        "CLAUDE.md": '- API @Design\\ Docs/api.md\n- Quoted @"quoted.md"\n- Mail me@email.md\n',
        "Design Docs/api.md": "api\n", "quoted.md": "q\n", "email.md": "e\n",
    })

    assert sorted(load_claude_code_project_files(tmp_path)) == ["CLAUDE.md", "Design Docs/api.md"]


def test_an_agents_md_read_without_a_claude_md_has_its_imports_staged(tmp_path):
    _project(tmp_path, {"AGENTS.md": "rule @docs/more.md\n", "docs/more.md": "more\n"})

    assert load_claude_code_project_files(tmp_path) == {"AGENTS.md": "rule @docs/more.md\n", "docs/more.md": "more\n"}


def test_an_absolute_import_inside_the_project_is_staged_by_its_project_path(tmp_path):
    root = tmp_path.resolve()
    _project(root, {"CLAUDE.md": f"@{root / 'docs/abs.md'}\n", "docs/abs.md": "abs\n"})

    assert sorted(load_claude_code_project_files(root)) == ["CLAUDE.md", "docs/abs.md"]


def test_imports_outside_the_project_are_named_not_staged(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n")
    project = _project(tmp_path / "project", {"CLAUDE.md": f"@~/home.md\n@{outside}\n@../outside.md\n"})

    assert load_claude_code_project_files(project) == {"CLAUDE.md": f"@~/home.md\n@{outside}\n@../outside.md\n"}
    names = ["CLAUDE.md: @~/home.md", f"CLAUDE.md: @{outside}", "CLAUDE.md: @../outside.md"]
    assert files_not_staged("claude-code", project) == names
    assert files_not_staged_reasons("claude-code", project) == dict.fromkeys(names, OUTSIDE_IMPORT_REASON)


def test_an_imported_env_file_and_claude_local_md_are_named_not_staged(tmp_path):
    _project(tmp_path, {
        "CLAUDE.md": "@config/.env.shared\n@config/.env.example\n@CLAUDE.local.md\n",
        "config/.env.shared": "TOKEN=x\n", "config/.env.example": "TOKEN=\n", "CLAUDE.local.md": "mine\n",
    })

    staged = load_claude_code_project_files(tmp_path)

    assert sorted(staged) == ["CLAUDE.md", "config/.env.example"]
    assert files_not_staged_reasons("claude-code", tmp_path) == {
        "CLAUDE.local.md": LOCAL_REASON,
        "CLAUDE.md: @CLAUDE.local.md": LOCAL_REASON,
        "CLAUDE.md: @config/.env.shared": ENV_IMPORT_REASON,
    }


def test_an_imported_file_is_not_named_as_left_out_and_has_no_reason_to_be(tmp_path):
    _project(tmp_path, {"CLAUDE.md": "@AGENTS.md\n@.claude/notes.md\n", "AGENTS.md": "a\n", ".claude/notes.md": "n\n"})
    staged = load_claude_code_project_files(tmp_path)

    assert files_not_staged("claude-code", tmp_path) == []
    assert not_staged_reason("claude-code", tmp_path, "AGENTS.md", staged) is None
    assert not_staged_reason("claude-code", tmp_path, ".claude/notes.md", staged) is None


def test_missing_imports_folders_and_a_cycle_are_left_alone(tmp_path):
    _project(tmp_path, {"CLAUDE.md": "@missing.md @docs @a.md\n", "docs/x.md": "x\n", "a.md": "@b.md\n", "b.md": "@a.md @CLAUDE.md\n"})

    assert sorted(load_claude_code_project_files(tmp_path)) == ["CLAUDE.md", "a.md", "b.md"]
    assert files_not_staged("claude-code", tmp_path) == []


def test_an_import_that_is_a_symlink_is_refused_since_claude_code_would_read_it(tmp_path):
    root = tmp_path.resolve()
    _project(root, {"CLAUDE.md": "@docs/link.md\n", "docs/target.md": "t\n"})
    (root / "docs/link.md").symlink_to("target.md")

    with pytest.raises(ValueError) as raised:
        load_claude_code_project_files(root)

    assert str(raised.value) == (
        f"{root / 'CLAUDE.md'} imports @docs/link.md, which Claude Code would read, but Whitney doesn't stage it: "
        "it is a symlink, or inside a symlinked folder. Replace the link with the file it points to"
    )


@pytest.mark.parametrize("folder", ["node_modules", ".venv"])
def test_an_import_inside_an_excluded_folder_is_refused(tmp_path, folder):
    root = tmp_path.resolve()
    _project(root, {"CLAUDE.md": f"@{folder}/rules.md\n", f"{folder}/rules.md": "r\n"})

    with pytest.raises(ValueError) as raised:
        load_claude_code_project_files(root)

    assert str(raised.value) == (
        f"{root / 'CLAUDE.md'} imports @{folder}/rules.md, which Claude Code would read, but Whitney doesn't stage it: "
        f"it is inside {folder}/, which is never staged"
    )


def test_an_import_that_is_not_utf8_is_refused(tmp_path):
    root = tmp_path.resolve()
    _project(root, {"AGENTS.md": "@data.md\n", "data.md": b"\xff\xfe"})

    with pytest.raises(ValueError) as raised:
        load_claude_code_project_files(root)

    assert str(raised.value) == (
        f"{root / 'AGENTS.md'} imports @data.md, which Claude Code would read, but Whitney doesn't stage it: it isn't UTF-8 text"
    )


def test_an_import_under_the_evals_path_is_refused(tmp_path):
    root = tmp_path.resolve()
    _project(root, {"CLAUDE.md": "@evals/rules.md\n", "evals/rules.md": "r\n"})

    with pytest.raises(ValueError) as raised:
        load_claude_code_project_files(root, evals_path=root / "evals")

    assert str(raised.value) == (
        f"{root / 'CLAUDE.md'} imports @evals/rules.md, which Claude Code would read, but Whitney doesn't stage it: "
        "it is under the evals path, whose files are never staged"
    )


def test_a_nested_import_is_refused_by_the_file_that_holds_it(tmp_path):
    root = tmp_path.resolve()
    _project(root, {"CLAUDE.md": "@docs/a.md\n", "docs/a.md": "@b.md\n", "docs/b.md": b"\xff"})

    with pytest.raises(ValueError, match=r"docs/a\.md imports @b\.md, "):
        load_claude_code_project_files(root)


def test_an_import_that_cannot_be_read_is_refused(tmp_path):
    root = tmp_path.resolve()
    _project(root, {"CLAUDE.md": "@locked.md\n", "locked.md": "r\n"})
    (root / "locked.md").chmod(0)
    try:
        with pytest.raises(ValueError, match=r"imports @locked\.md, .*: it can't be read: Permission denied$"):
            load_claude_code_project_files(root)
    finally:
        (root / "locked.md").chmod(0o644)


def test_files_not_staged_names_imports_even_where_the_loader_refuses_one(tmp_path):
    _project(tmp_path, {"CLAUDE.md": "@~/x.md @data.md\n", "data.md": b"\xff"})

    assert files_not_staged("claude-code", tmp_path) == ["CLAUDE.md: @~/x.md"]


def test_an_unreadable_instruction_file_names_no_imports(tmp_path):
    _project(tmp_path, {"CLAUDE.md": b"\xff @~/x.md\n"})

    assert files_not_staged("claude-code", tmp_path) == []


def test_every_import_that_cannot_be_staged_is_refused_in_one_message(tmp_path):
    root = tmp_path.resolve()
    _project(root, {"CLAUDE.md": "@one.md @two.md\n", "one.md": b"\xff", "two.md": b"\xfe"})

    with pytest.raises(ValueError) as raised:
        load_claude_code_project_files(root)

    assert str(raised.value) == (
        f"{root / 'CLAUDE.md'} imports @one.md, which Claude Code would read, but Whitney doesn't stage it: it isn't UTF-8 text; "
        f"{root / 'CLAUDE.md'} imports @two.md, which Claude Code would read, but Whitney doesn't stage it: it isn't UTF-8 text"
    )


def test_a_file_imported_twice_is_followed_once(tmp_path):
    _project(tmp_path, {"CLAUDE.md": "@a.md and again @a.md\n", "a.md": "@~/x.md\n"})

    assert files_not_staged("claude-code", tmp_path) == ["a.md: @~/x.md"]


def test_an_import_right_after_a_code_span_is_still_an_import(tmp_path):
    # Only the span is passed over; the text after it is read as any other text is.
    _project(tmp_path, {"CLAUDE.md": "`code`@after.md\n", "after.md": "a\n"})

    assert sorted(load_claude_code_project_files(tmp_path)) == ["CLAUDE.md", "after.md"]


def test_an_unreadable_instruction_file_does_not_stop_the_next_ones_imports_being_named(tmp_path):
    _project(tmp_path, {"CLAUDE.md": b"\xff", ".claude/CLAUDE.md": "@~/x.md\n"})

    assert files_not_staged("claude-code", tmp_path) == [".claude/CLAUDE.md: @~/x.md"]


def test_an_import_under_the_evals_path_is_named_with_the_rest_of_dot_claude(tmp_path):
    _project(tmp_path, {"CLAUDE.md": "@.claude/notes.md\n", ".claude/notes.md": "n\n"})

    assert files_not_staged("claude-code", tmp_path, evals_path=tmp_path / ".claude") == [".claude/notes.md"]
    assert files_not_staged("claude-code", tmp_path) == []
