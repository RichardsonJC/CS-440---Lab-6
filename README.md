# contrib-agent

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

## Setup (Windows / PowerShell)

```powershell
# Prereqs: Python 3.10+, git, GitHub CLI, Claude Code CLI
winget install GitHub.cli
gh auth login
irm https://claude.ai/install.ps1 | iex     # Claude Code (the SDK drives it)

cd contrib-agent
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Optional, to match CONTRIBUTING.md's "free-threaded Python 3.15t+":
#   install 3.15 from python.org and tick "free-threaded binaries".
#   The checks use py -3.15t automatically if present.

$env:ANTHROPIC_API_KEY = "sk-ant-..."     # or be logged in to Claude Code
```

On Linux/SSH, use `python3 -m venv .venv && source .venv/bin/activate` and
`export ANTHROPIC_API_KEY=...`.

## Run

```powershell
python agent.py --local-only   # practice: clones upstream read-only, no GitHub account used
python agent.py --dry-run      # creates/syncs your fork, commits locally, no push or PR
python agent.py                # full run: pushes the branch and opens the PR on your fork
python agent.py --model claude-sonnet-5-5   # cheaper model
```

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
python -m pytest tests
```
