# Sandbox image for running Claude Code under Inspect, as ../Dockerfile is for Kilo.
# Claude Code and the base image are pinned so results are comparable across runs;
# bump deliberately (update the digest with the tag, from `docker pull`).
FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da

ARG CLAUDE_CODE_VERSION=2.1.322

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates python3 curl \
 && rm -rf /var/lib/apt/lists/*

RUN git config --system user.name "Eval Agent" \
 && git config --system user.email "agent@example.invalid" \
 && git config --system init.defaultBranch main \
 && mkdir -p /workspace /opt/claude \
 && chown node:node /workspace /opt/claude

# As Kilo's image: npm's install scripts run as the unprivileged user, and root then takes
# the folder back, so an agent can't change Claude Code while it runs.
USER node
RUN npm install -g --prefix /opt/claude "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force
USER root
RUN chown -R root:root /opt/claude \
 && ln -s /opt/claude/bin/claude /usr/local/bin/claude \
 && claude --version

# Claude Code and its tools run as the unprivileged user, as Kilo's do.
USER node
WORKDIR /workspace
CMD ["sleep", "infinity"]
