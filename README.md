# contrib-agent
**Fork used for the contribution:** https://github.com/RichardsonJC/Python
**Pull request:** [_(added after the live run)_](https://github.com/RichardsonJC/Python/pull/1)

An agent that resolves an issue in TheAlgorithms/Python **on your fork only**,
following the repo's CONTRIBUTING.md. Built on the Claude Agent SDK.

Target: [issue #15633](https://github.com/TheAlgorithms/Python/issues/15633):
`alternative_password_generator` returns a password longer than requested when
the required characters exceed the length.

## How it works

| Stage | Who decides | What happens |
|---|---|---|
| Fork setup | Python code | `gh repo fork` (if needed), verify it's really a fork of upstream, sync fork's `master`, clone, branch `fix/issue-15633` from `upstream/master`, **disable the upstream push URL**, `gh repo set-default <you>/Python`, `pre-commit install` |
| Fix | Agent | Reads issue + the clone's CONTRIBUTING.md, reproduces the bug, edits, adds doctests, runs checks |
| Checks | Python code (tool) | `ruff check`, `ruff format --check`, `python -m doctest -v`, `ty check` (informational), `pre-commit run --files` |
| Commit / push | Python code (tool) | Rejects protected files and upstream references; pushes to `origin` only after verifying origin is your fork |
| PR | Python code (tool) | Re-runs checks and refuses if failing, requires `Fixes #15633`, creates the PR with an explicit `--repo <you>/Python --base master` |
| Final verify | Python code | Re-runs checks independently and saves the diff |

### Guardrails that keep it off upstream

1. **No credentials for the agent.** Its subprocesses run with `GH_TOKEN=""`, an
   empty `GH_CONFIG_DIR`, an empty global gitconfig, and `GIT_CONFIG_NOSYSTEM=1`
   (drops Git Credential Manager on Windows). Even `python -c "subprocess…git push"` fails.
2. **Bash allow-list hook.** One command at a time; no `gh`, no `git push/remote/config`,
   no pipes/redirects/`&&`/`$(...)`.
3. **File hook.** No edits to README.md, DIRECTORY.md, CONTRIBUTING.md, `.github/`,
   notebooks, non-snake_case `.py` files, new directories, or anything outside the clone.
4. **Upstream push URL disabled** in the clone.
5. **No cross-references.** Commit messages and PR text containing
   `TheAlgorithms/Python#…` or an upstream URL are refused. On a fork, those
   would post a "mentioned this" event on the real upstream issue.
   A bare `Fixes #15633` satisfies CONTRIBUTING.md and resolves only within your fork.
6. **Explicit `--repo` on `gh pr create`.** Without it, `gh` defaults a fork's PRs
   to the **parent** repo. This is the easiest way to accidentally contribute upstream.

## Design decisions

- **Code does the Git plumbing; the model only does the fix.** Forking, branching,
  pushing and opening the PR are the same every time and must never go wrong, so they
  are plain Python with checks. The model is used where judgment is needed: reading
  the issue, choosing the fix, writing doctests.
- **Guardrails are enforced in code, not just in the prompt.** A prompt can be ignored;
  a hook that denies the call, or a shell with no credentials, cannot. The prompt
  explains the rules so the agent rarely hits a guard, and the guards make sure it
  can't break them when it does.
- **Several layers instead of one.** The command allow-list, credential-free env,
  disabled push URL, origin check and explicit `--repo` each cover a different way
  to reach upstream, so one mistake isn't enough.
- **CONTRIBUTING.md is read from the fresh clone.** The agent follows the rules that
  exist on the day it runs, not a copy that may be outdated. A bundled copy is the fallback.
- **The PR tool re-runs the checks itself.** The agent's claim that checks passed isn't
  trusted; the PR can't be opened while `ruff check` or the doctests fail.
- **Three modes.** `--local-only` and `--dry-run` let you test safely before a live run.
- **Everything is logged.** The transcript, tool audit log, diff and PR body make each
  run reviewable after the fact.

## Run it in Docker (recommended)

The image contains Python, git, the GitHub CLI, free-threaded Python 3.15t,
and the Claude Agent SDK, which bundles its own Claude Code binary. Your
machine only needs Docker Desktop; Claude Code is never installed on it.
Running in a container is also one more layer of isolation: the agent can
only see this project folder and its own workspace volume.

**1. Credentials.** Copy `.env.example` to `.env` and fill it in. `.env` is gitignored.
- Claude credentials, **one of**:
  - `CLAUDE_CODE_OAUTH_TOKEN` (Claude Pro/Max subscription; counts against your
    plan's usage, no API billing). After step 2, generate it inside the container:
    `docker run --rm -it --entrypoint claude contrib-agent setup-token`. Sign in at
    the URL it prints, paste the code back if asked, and copy the token it shows.
  - `ANTHROPIC_API_KEY` from console.anthropic.com, billed per token. A run costs well under $1.
  Set only one; if both are set, the API key wins.
- `GH_TOKEN` (not needed for `--local-only`): least privilege, so the token can
  only touch your fork:
  1. Fork TheAlgorithms/Python in the browser (Fork button). Keep "copy the
     master branch only" checked.
  2. GitHub → Settings → Developer settings → Fine-grained tokens → Generate.
     Repository access: *Only select repositories* → `<you>/Python`.
     Permissions: **Contents: Read and write**, **Pull requests: Read and write**.
     Set a short expiration.
- `GIT_USER_EMAIL`: your GitHub noreply address (Settings → Emails).

**2. Build** (once, and again after changing `requirements.txt` or the Dockerfile):

```powershell
docker build -t contrib-agent .
```

**3. Run** from the project folder:

```powershell
docker run --rm --env-file .env -v "${PWD}:/app" -v contrib-work:/work contrib-agent --local-only
docker run --rm --env-file .env -v "${PWD}:/app" -v contrib-work:/work contrib-agent --dry-run
docker run --rm --env-file .env -v "${PWD}:/app" -v contrib-work:/work contrib-agent
```

The first is practice with no GitHub account involved, the second commits on
your fork's branch without pushing, and the third pushes and opens the PR on
your fork. Add `--model claude-sonnet-5-5` at the end for a cheaper model.
Run artifacts appear in `runs/` on your machine; the TheAlgorithms clone stays
in the `contrib-work` volume (`docker volume rm contrib-work` to reset it).

## Run it without Docker

Needs Python 3.10+, git and the GitHub CLI on your machine. Note that
`pip install claude-agent-sdk` puts its bundled Claude Code binary in your
Python environment.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
gh auth login
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python agent.py --local-only    # or --dry-run, or no flag for the live run
```

## What a run produces

The PR is opened as `<you>/Python: fix/issue-15633 → <you>/Python: master`.
Nothing goes to `TheAlgorithms/Python`.

Each run writes to `runs/<timestamp>/`:
- `transcript.md`: the agent's reasoning, tool calls and results
- `tool_audit.jsonl`: every Bash/file action and whether a guard denied it
- `change.diff`, `final_checks.txt`, `pr_body.md`

`example_run/` holds a real `--local-only` run (10 turns, $0.33).

## Retargeting

Change `ISSUE_NUMBER` in `config.py` (or pass `--issue N`) and save the issue
text to `issues/issue_N.md` as a fallback. The task steps in `agent.py`
(`TASK_PROMPT`) mention the password function's edge cases; make those generic
for other issues.

## Tests

```powershell
docker run --rm -v "${PWD}:/app" --entrypoint python contrib-agent -m pytest tests
```
