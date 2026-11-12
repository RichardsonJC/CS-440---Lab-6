# Everything the agent needs lives in this image, so nothing (Claude Code
# included) is installed on the host. The claude-agent-sdk wheel bundles its
# own Claude Code binary; there is no separate install step.
FROM python:3.13-slim

# git + GitHub CLI for the fork/push/PR plumbing.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# Free-threaded Python 3.15t, which CONTRIBUTING.md targets. The checks use it
# automatically for doctests when `python3.15t` is on PATH.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install 3.15t \
 && ln -s "$(uv python find 3.15t)" /usr/local/bin/python3.15t \
 && python3.15t -VV

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && ln -s "$(python -c 'import claude_agent_sdk, os; print(os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", "claude"))')" \
      /usr/local/bin/claude \
 && claude --version
# The symlink above exposes the SDK's bundled Claude Code inside the image
# only, so `claude setup-token` can run here instead of on the host.

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh \
 && useradd --create-home agent \
 && mkdir /work && chown agent:agent /work

# The TheAlgorithms clone goes in /work (a Docker volume), not in the project
# folder, so thousands of files don't land in OneDrive or slow the bind mount.
ENV CONTRIB_WORK_ROOT=/work PYTHONUNBUFFERED=1
USER agent
WORKDIR /app
ENTRYPOINT ["docker-entrypoint.sh"]
