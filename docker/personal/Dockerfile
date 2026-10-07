# Build from the repository root. No personal state enters the image.
FROM node:24-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
COPY test/fixtures/work_contract_v1.json /build/test/fixtures/work_contract_v1.json
RUN npm run build
WORKDIR /build/cao_mcp_apps
COPY cao_mcp_apps/package.json cao_mcp_apps/package-lock.json ./
RUN npm ci --ignore-scripts
COPY cao_mcp_apps/ ./
RUN npm run build:all

FROM debian:bookworm-slim AS worker-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY src/cli_agent_orchestrator/backends/docker_work_supervisor.c /build/supervisor.c
ARG CAO_WORK_SUPERVISOR_SOURCE_SHA256
RUN test -n "$CAO_WORK_SUPERVISOR_SOURCE_SHA256" && \
    test "$(sha256sum /build/supervisor.c | cut -d ' ' -f 1)" = "$CAO_WORK_SUPERVISOR_SOURCE_SHA256" && \
    cc -static -O2 -s -Wall -Wextra -Werror \
      -DCAO_SUPERVISOR_SOURCE_SHA256=\"$CAO_WORK_SUPERVISOR_SOURCE_SHA256\" \
      /build/supervisor.c -o /build/cao-work-supervisor

FROM scratch AS worker-rootfs
COPY --from=worker-build /build/cao-work-supervisor /cao-work-supervisor
ARG CAO_WORK_SUPERVISOR_SOURCE_SHA256
LABEL org.cao.work.supervisor.protocol="1"
LABEL org.cao.work.supervisor.source_sha256="${CAO_WORK_SUPERVISOR_SOURCE_SHA256}"

FROM ghcr.io/astral-sh/uv:0.11.8 AS uv
FROM docker:29.8.1-cli AS docker-cli
FROM python:3.12-slim-trixie AS application
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=frontend /usr/local/ /usr/local/
RUN apt-get update && apt-get install -y --no-install-recommends \
      nginx tmux git ca-certificates procps libatomic1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
ARG CLAUDE_CODE_VERSION=2.1.285
ARG CODEX_VERSION=0.159.3
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force && claude --version && codex --version
WORKDIR /opt/cao
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY scripts/hatch_build_tui_tag.py scripts/
COPY src/ src/
COPY --from=frontend /build/src/cli_agent_orchestrator/web_ui/ src/cli_agent_orchestrator/web_ui/
COPY --from=frontend /build/src/cli_agent_orchestrator/ext_apps/apps_static/ src/cli_agent_orchestrator/ext_apps/apps_static/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv CAO_TUI_PLATFORM_WHEEL=0
RUN uv sync --frozen --no-dev --all-extras --no-editable \
    && test -f /opt/venv/lib/python3.12/site-packages/cli_agent_orchestrator/web_ui/index.html
RUN apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0t64 libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 \
      libdbus-1-3 libcups2t64 libxcb1 libxkbcommon0 libasound2t64 libgbm1 \
      libx11-6 libxext6 libcairo2 libpango-1.0-0 libxcomposite1 libxdamage1 \
      libxfixes3 libxrandr2 libatspi2.0-0t64 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY scripts/personal_deployment.py scripts/local_work_docker_demo.py scripts/docker_personal_runtime.py scripts/docker_windows_mcp.py scripts/
RUN printf '#!/bin/sh\nexec /opt/venv/bin/python /opt/cao/scripts/docker_windows_mcp.py "$@"\n' > /usr/local/bin/cmd.exe \
    && chmod 755 /usr/local/bin/cmd.exe
ENV PATH="/opt/venv/bin:/opt/cao/host-tools:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    PYTHONUNBUFFERED=1 CAO_PERSONAL_ROOT=/data CAO_WINDOWS_MCP_SOCKET=/opt/cao/windows-mcp/mcp.sock
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=20s --start-period=90s --retries=3 \
  CMD ["python", "/opt/cao/scripts/docker_personal_runtime.py", "healthcheck"]
ENTRYPOINT ["python", "/opt/cao/scripts/docker_personal_runtime.py"]
