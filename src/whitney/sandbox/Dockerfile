# Sandbox image for running Kilo CLI under Inspect.
# Kilo and the base image are pinned so results are comparable across runs;
# bump deliberately (update the digest with the tag, from `docker pull`).
FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da

ARG KILO_VERSION=7.8.18

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/*

RUN git config --system user.name "Eval Agent" \
 && git config --system user.email "agent@example.invalid" \
 && git config --system init.defaultBranch main \
 && mkdir -p /workspace /opt/kilo \
 && chown node:node /workspace /opt/kilo

# npm runs the install scripts of Kilo's packages (one links its platform
# binary into place), so it runs as the unprivileged user, into a folder that
# user owns for the install only. Root then takes the folder back, so an agent
# can't change Kilo while it runs.
USER node
RUN npm install -g --prefix /opt/kilo "@kilocode/cli@${KILO_VERSION}" \
 && npm cache clean --force
USER root
RUN chown -R root:root /opt/kilo \
 && ln -s /opt/kilo/bin/kilo /usr/local/bin/kilo \
 && kilo --version

# Kilo and its tools run as the base image's unprivileged user: as root, an
# agent could replace the git the scorer runs, or anything else in the image.
USER node
WORKDIR /workspace
CMD ["sleep", "infinity"]
