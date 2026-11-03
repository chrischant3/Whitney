Scenario: Run the evals against a model via Kilo using the Kilo Template
Given I have Kilo configured
When I run the evals against a model
Then the evals are run against the model using my Kilo configuration

Scenario: Run the evals directly agasint Claude Code using the Claude Code Template
Given I have Claude Code configured
When I run the evals against a model
Then the evals are run against the model using my Claude Code configuration
