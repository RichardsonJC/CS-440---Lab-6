#!/bin/sh
# Container startup: check credentials, wire up git, then run the agent.
set -e

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in." >&2
    exit 1
fi

case " $* " in
    *" --local-only "*) ;;
    *)
        if [ -z "$GH_TOKEN" ]; then
            echo "GH_TOKEN is not set (needed for --dry-run and live runs). See .env.example." >&2
            exit 1
        fi
        # Lets git push to the fork using the token. Only this parent process
        # has it; the agent's own commands run with credentials removed.
        git config --global credential.https://github.com.helper '!gh auth git-credential'
        ;;
esac

git config --global user.name "${GIT_USER_NAME:-contrib-agent}"
git config --global user.email "${GIT_USER_EMAIL:-contrib-agent@localhost}"
git config --global --add safe.directory '*'

exec python /app/agent.py "$@"
