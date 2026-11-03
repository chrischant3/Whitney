## Intent of the project

### Problem

- Evaluation of AI rules, instructions and skills is misunderstood.
- Development teams, auditors and infosec need to have confidence that, when an AI is given instructions, it will follow them.
- Companies often need to standardise their processes to give them control.
- There may be a need to evaluate individual AI platform providers like Claude as well as harnesses like Kilo.ai.

### Proposed solution

Inspiration: https://github.com/addyosmani/agent-skills/tree/6ca0cd7db39b41b1c37e26d335c507ee92382c6d/evals

I like how easy agent-skills is to read.

Use https://inspect.aisi.org.uk/ to test the instructions against IFEvalCode: https://ukgovernmentbeis.github.io/inspect_evals/evals/ifevalcode/index.html

The solution must:

- be structured so that it is easy to understand.
- provide a template which can be used to standardise configs, skills, AGENTS.md files in a company.
- enable the user to amend the template's AGENTS.md, skills, agents and config files and add their own test scenarios.
- The solution is designed to be forked and tailored to the team's needs.
- The intent is that the evals framework will work with Kilo.ai.
- Use Python and inspect-ai for building the evaluation framework.
- Results show how closely the AI follows instructions, wherever the instructions are loaded from.
- Test scenarios will be written using JSON.
  - Each file (AGENTS.md, rules/instructions, agents and skills) has its own scenario JSON file with the following naming convention:
    - `skill-name.json` - name of the skill
    - `agents-md.json` - name of the file with the file type included.
    - `kilo-jsonc.json` - name of a config file with the file type included.
- Tests provide evidence that triggers and routing behave as desired.
  - See the Inspiration link above: "Positive prompts rank their skill top-k; negative prompts don't; no two descriptions near-collide"
- Test and provide evidence that the agent following a set of instructions or using a skill satisfies the expectations.
- Test configuration to ensure permissions are not ignored.
- Test context hierarchy.

### Proposed structure

In order to make the framework easy to read, extend and maintain, I propose the following structure:

- `docs/` - location for documents explaining the templates, hierarchy & load order, how to add tests when updating a template and how to add tests when adding to the template.
- `evals/` - evals directory
- `evals/configuration` - evals for testing the configuration and permissions (kilo.jsonc)
- `evals/agents-md.json` - evals for testing the AGENTS.md file
- `scripts/` - the scripts for running the evals
- `template/`
- `template/.kilo` - the Kilo template
- `template/.kilo/AGENTS.md` - the template's AGENTS.md file containing the core rules & behaviours for every session.
- `template/kilo.jsonc` - the main config file for Kilo, which defines global permissions, approved models etc.
- `README.md` - explains what the project is and how to run the evals and points the reader to the relevant docs

### Considerations

The structure of https://github.com/addyosmani/agent-skills is a great example of a clean, easy to understand eval project.

### MVP Acceptance Criteria

The MVP tests one instruction and tests that permissions are honoured. It includes:

- a Kilo template containing `AGENTS.md` with a single rule "- Never commit .env files or secrets" which can be used by any team.
- The evals framework and its evals report how accurately the instructions are followed.
- The evals framework tests the template's instructions when used in both Kilo CLI and the VS Code extension.

### Future work

Ignore this section until the MVP is complete.

- test hierarchy
- test one skill
- test one agent
- test verification hook (for Kilo we'll test the code review feature, REVIEW.md: https://kilo.ai/docs/automate/code-reviews/overview)

#### Structure

- `template/.kilo/REVIEW.md` - code review agent runs at the end of each run. Used to start the verify subagent
- `template/.kilo/agents/` -
- `template/.kilo/references/` - reference files
- `template/.kilo/skills/` -
- `evals/agents/` - evals for testing agent files
- `evals/skills/` - evals for testing skills

#### Phase two

- Add the `REVIEW.md` with the verifying agent, and the evals that prove they work.

#### Phase three

- Add the test-quality-check skill containing a single test pattern to check for and the evals that prove it works.
- Add a test-review agent which runs as a subagent invoked by REVIEW.md and the evals that prove it checks the tests follow the test pattern and that it has read-only access to the code.

#### Phase four

- Implement tests for using hooks. (Kilo calls these tools.)
