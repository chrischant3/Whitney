# Agent rules

- Never edit files under vendor/. Vendored code is a pinned copy of an upstream library: change our own code around it, and tell the user what should be fixed upstream.

# Data handling

- Never copy customer data out of exports/. Make up test data instead.
- Never point code at production URLs. Use the test environment.
