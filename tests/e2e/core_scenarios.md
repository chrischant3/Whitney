Scenario: Run the evals against a model via Kilo using the Kilo template
Given I have Kilo configured
When I run the evals against a model
Then the evals are run against the model using my Kilo configuration

Scenario: Run the evals directly against Claude Code using the Claude Code template
Given I have Claude Code configured
When I run the evals against a model
Then the evals are run against the model using my Claude Code configuration
