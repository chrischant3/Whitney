# Sandbox image for running Kilo CLI under Inspect.
# Kilo is pinned so results are comparable across runs; bump deliberately.
FROM node:22-bookworm-slim

ARG KILO_VERSION=7.8.18

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g "@kilocode/cli@${KILO_VERSION}" \
 && kilo --version

RUN git config --system user.name "Eval Agent" \
 && git config --system user.email "agent@example.invalid" \
 && git config --system init.defaultBranch main

WORKDIR /workspace
CMD ["sleep", "infinity"]
