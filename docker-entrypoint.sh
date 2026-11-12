#!/bin/sh
# Container startup: check credentials, wire up git, then run the agent.
set -e

# --env-file passes blank lines as empty variables; drop them so an empty
# ANTHROPIC_API_KEY can't shadow the subscription token.
[ -z "$ANTHROPIC_API_KEY" ] && unset ANTHROPIC_API_KEY
[ -z "$CLAUDE_CODE_OAUTH_TOKEN" ] && unset CLAUDE_CODE_OAUTH_TOKEN

# Either a Claude subscription token (from `claude setup-token`) or an API key.
if [ -z "$CLAUDE_CODE_OAUTH_TOKEN" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env (see .env.example)." >&2
    exit 1
fi
if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] && [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "Both credentials set; ANTHROPIC_API_KEY takes precedence and will be billed." >&2
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
