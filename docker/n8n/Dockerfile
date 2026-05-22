# Custom n8n image: official (hardened) n8n + docker CLI binary so Execute
# Command nodes can `docker exec clawstreet-worker python /app/<layer>/scripts/...`.
# Hardened base has no apk/apt — copy the statically-linked docker CLI binary
# from the official docker image instead.
FROM docker:27-cli AS docker-cli

FROM n8nio/n8n:latest
USER root
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
USER node
