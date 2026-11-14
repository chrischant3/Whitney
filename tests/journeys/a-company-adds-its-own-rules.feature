Feature: A company adds its own rules and their tests
  As a team standardising instructions across several projects
  I want to add our own rules and tests without changing Whitney itself
  So that every team's assistant is held to the same standard

  The examples/ folder holds projects a team copies and edits. Each pairs an
  AGENTS.md rule with an eval file that tests it using one of Whitney's
  checks. A fake `kilo` on PATH stands in for the assistant.

  Scenario Outline: A copy of an example is ready to run
    Given a copy of examples/<example>
    When I run "whitney validate --target <target> --project-path <example> --evals-path <example>/evals"
    Then whitney exits 0
    And it prints "0 problem(s)"

    Examples:
      | example            | target      |
      | consumer           | kilo        |
      | consumer           | claude-code |
      | colocated-tests    | kilo        |
      | skipped-tests      | kilo        |
      | tests-before-commit | kilo       |

  Scenario: A rule that asks for something to be done is tested with file_added
    Given a copy of examples/colocated-tests
    And an assistant that puts a new test next to its module only when AGENTS.md says to
    When I run "whitney run --target kilo --model mockllm/model --epochs 3 --project-path colocated-tests --evals-path colocated-tests/evals"
    Then the report shows "add-discount" at 0/3 without AGENTS.md and 3/3 with it
    And whitney exits 0
