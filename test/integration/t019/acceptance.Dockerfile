# Reproducible Docker orchestrator for the existing pinned Ubuntu QEMU guest.
FROM ubuntu:24.04@sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4
COPY --from=ghcr.io/astral-sh/uv:0.12.4@sha256:0ff11fbfed5cb87a930fa471748f03bd78cbc15a4fc09f18b06a15e0ead32370 /uv /usr/local/bin/uv

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y \
        bash ca-certificates curl git openssh-client sudo python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
