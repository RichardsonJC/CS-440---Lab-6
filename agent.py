"""
Contribution agent for TheAlgorithms/Python, built on the Claude Agent SDK.

Pipeline:
  1. (code)  Fork upstream into your account, clone the fork, branch from
             upstream/master, disable pushing to upstream.
  2. (agent) Read the issue and CONTRIBUTING.md, reproduce the bug, fix it,
             add doctests, run the checks, commit, open a PR -- on the fork.
  3. (code)  Every write the agent asks for goes through a guarded tool that
             re-verifies the target is the fork and that checks pass.

Usage:
  python agent.py                     # full run against your fork
  python agent.py --dry-run           # fork + fix + local commit, no push/PR
  python agent.py --local-only        # no GitHub account touched at all
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

import config
import github_ops
from guards import check_bash_command, check_editable_path

BUNDLED_CONTRIBUTING = Path(__file__).resolve().parent / "CONTRIBUTING.md"
ISSUES_DIR = Path(__file__).resolve().parent / "issues"


# ---------------------------------------------------------------- context


def fetch_issue(issue_number: int) -> dict:
    """
    Read-only lookup of the upstream issue. Tries the public API, then `gh`,
    then a saved copy at issues/issue_<n>.md (first line = title).
    """
    url = f"https://api.github.com/repos/{config.UPSTREAM_REPO}/issues/{issue_number}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        return {
            "title": data["title"],
            "body": data.get("body") or "",
            "state": data["state"],
        }
    except OSError as error:
        print(f"[issue] API lookup failed ({error}); trying fallbacks")
    try:
        data = github_ops.fetch_issue(issue_number)
        return {"title": data["title"], "body": data["body"], "state": data["state"]}
    except (RuntimeError, OSError) as error:
        print(f"[issue] gh lookup failed ({error}); using saved copy")
    saved = ISSUES_DIR / f"issue_{issue_number}.md"
    if not saved.exists():
        sys.exit(f"Could not fetch issue #{issue_number} and no {saved} exists.")
    title, _, body = saved.read_text(encoding="utf-8").partition("\n")
    return {"title": title.lstrip("# ").strip(), "body": body.strip(), "state": "open"}


def load_contributing(repo_dir: Path) -> str:
    """Prefer the CONTRIBUTING.md in the fresh clone; fall back to the bundled copy."""
    for candidate in (repo_dir / "CONTRIBUTING.md", BUNDLED_CONTRIBUTING):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    sys.exit("No CONTRIBUTING.md found.")


def build_system_prompt(
    issue_number: int, issue: dict, contributing: str, ctx: github_ops.ForkContext
) -> str:
    return f"""You are a careful open-source contributor fixing one issue in TheAlgorithms/Python.

You are working in a clone of the user's FORK ({ctx.fork_repo}) on branch `{ctx.branch}`.
This is a class assignment: you must NOT contribute to the upstream project.
Never push, comment, open issues, or open PRs anywhere except through the
provided tools, which only target the fork. Bash has no GitHub credentials.

# Rules you must follow (from the project's CONTRIBUTING.md, reproduced below)
- Keep the change minimal and focused on the issue. Do not refactor unrelated code.
- Raise Python exceptions (e.g. ValueError) on erroneous input.
- Add doctests covering BOTH valid input and the erroneous input from the issue.
- Type hints on parameters and return values. Descriptive names; no new single-letter names.
- Comments explain *why*, appear before the code they describe, and lines stay <= 88 chars.
- Do not edit README.md or DIRECTORY.md. Do not create new directories.
- Code must target free-threaded Python 3.15t+: no reliance on the GIL or shared mutable globals.
- `ruff check` must pass; run `ruff format`. Run doctests. `ty` is informational.
- The PR description must contain the exact line `Fixes #{issue_number}`.
  Write it exactly like that. Do NOT write `TheAlgorithms/Python#...` or link to
  upstream URLs: on a fork that cross-references the upstream issue.
- Do not create issues. Do not ask for assignment.

# Tools
- Read / Edit / Write / Glob / Grep for files.
- Bash for one command at a time (python, ruff, pre-commit, git status/diff/log).
  No pipes, redirects, `&&`, or `gh`.
- mcp__contrib__run_checks: runs ruff check, ruff format --check, doctests, ty and
  pre-commit on changed files. Use it before committing.
- mcp__contrib__commit_and_push: commits all changes and pushes the branch to the fork.
- mcp__contrib__open_pr: opens the PR on the fork (re-runs checks; refuses if failing).

# Issue #{issue_number}: {issue["title"]}  (state: {issue["state"]})
{issue["body"]}

# CONTRIBUTING.md
{contributing}
"""


TASK_PROMPT = """Resolve issue #{issue_number}. Work through these steps in order:

1. Read the file(s) named in the issue and understand the current behaviour.
2. Reproduce the bug with a single `python3 -c "..."` command and note the output.
3. Decide on the fix. Prefer the smallest change that makes the function honest
   about its contract and matches the repo's conventions (ValueError on bad input).
   Check whether other callers (e.g. a main() in the same file) need a tweak.
4. Implement it. Add a docstring if the function lacks one, with doctests for:
   normal lengths, the edge case length == len(required chars), and the
   erroneous case from the issue (expect ValueError).
5. Run `ruff format <file>`, then call run_checks. Fix anything that fails.
6. Re-run your reproduction command and confirm the bug is gone.
7. Call commit_and_push with a short imperative commit message, e.g.
   "Raise ValueError when required chars exceed password length".
8. Call open_pr. Title: concise, imperative. Body sections:
   - "Describe your change" (what and why, 2-4 sentences)
   - the line `Fixes #{issue_number}`
   - a "Checklist" in TheAlgorithms' style ([x] items) covering: ruff check passes,
     doctests added for valid and invalid input, type hints, no README/DIRECTORY edits,
     single file changed. Do NOT tick "this is my own work"; instead add a line
     disclosing that the change was produced by an AI agent under the author's direction.
   - "Verification": the before/after reproduction output.
9. Finish with a short summary: files changed, PR URL (or dry-run note), check results.
"""


# ---------------------------------------------------------------- tools


def build_tool_server(
    ctx: github_ops.ForkContext, issue_number: int, dry_run: bool, run_dir: Path
):
    def text_result(text: str, is_error: bool = False) -> dict:
        return {"content": [{"type": "text", "text": text}], "is_error": is_error}

    @tool(
        "run_checks",
        "Run ruff, doctests, ty and pre-commit on files changed on this branch.",
        {},
    )
    async def run_checks_tool(args: dict) -> dict:
        report = await asyncio.to_thread(github_ops.run_checks, ctx.repo_dir)
        verdict = "ALL BLOCKING CHECKS PASS" if report.passed else "SOME CHECKS FAIL"
        return text_result(f"{verdict}\n\n{report.text}", is_error=not report.passed)

    @tool(
        "commit_and_push",
        "Commit all changes and push the branch to the user's fork.",
        {"message": str},
    )
    async def commit_tool(args: dict) -> dict:
        try:
            result = await asyncio.to_thread(
                github_ops.commit_and_push, ctx, args["message"], dry_run
            )
            return text_result(result)
        except (github_ops.GuardError, RuntimeError) as error:
            return text_result(f"Refused: {error}", is_error=True)

    @tool(
        "open_pr",
        "Open (or update) the pull request on the user's fork.",
        {"title": str, "body": str},
    )
    async def pr_tool(args: dict) -> dict:
        try:
            result = await asyncio.to_thread(
                github_ops.open_pr,
                ctx,
                args["title"],
                args["body"],
                issue_number,
                dry_run,
                run_dir / "pr_body.md",
            )
            return text_result(result)
        except (github_ops.GuardError, RuntimeError) as error:
            return text_result(f"Refused: {error}", is_error=True)

    return create_sdk_mcp_server(
        name="contrib", tools=[run_checks_tool, commit_tool, pr_tool]
    )


# ---------------------------------------------------------------- hooks


def deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def build_hooks(repo_dir: Path, audit_log: Path) -> dict:
    def audit(entry: dict) -> None:
        with audit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    async def guard_bash(input_data, tool_use_id, context):
        command = input_data.get("tool_input", {}).get("command", "")
        reason = check_bash_command(command)
        audit({"tool": "Bash", "command": command, "denied": reason})
        return deny(reason) if reason else {}

    async def guard_file_write(input_data, tool_use_id, context):
        tool_input = input_data.get("tool_input", {})
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        reason = check_editable_path(path, repo_dir)
        audit({"tool": input_data.get("tool_name"), "path": path, "denied": reason})
        return deny(reason) if reason else {}

    return {
        "PreToolUse": [
            HookMatcher(matcher="Bash", hooks=[guard_bash]),
            HookMatcher(
                matcher="Edit|Write|MultiEdit|NotebookEdit", hooks=[guard_file_write]
            ),
        ]
    }


def credential_free_env(scratch: Path) -> dict[str, str]:
    """
    Environment for the agent's subprocesses with GitHub credentials removed, so
    even `python -c "import subprocess; ..."` cannot push or call the API.
    Our own tools run in this parent process and keep the real credentials.
    """
    empty_gitconfig = scratch / "empty.gitconfig"
    empty_gitconfig.write_text(
        "[user]\n\tname = contrib-agent\n\temail = agent@localhost\n", encoding="utf-8"
    )
    gh_dir = scratch / "gh-empty"
    gh_dir.mkdir(exist_ok=True)
    return {
        "GH_TOKEN": "",
        "GITHUB_TOKEN": "",
        "GH_CONFIG_DIR": str(gh_dir),
        "GIT_CONFIG_GLOBAL": str(empty_gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",  # drops Git Credential Manager on Windows
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
    }


# ---------------------------------------------------------------- run


class Transcript:
    """Console output plus a clean markdown transcript for the assignment write-up."""

    def __init__(self, path: Path):
        self.path = path
        self.path.write_text("# Agent transcript\n\n", encoding="utf-8")

    def write(self, markdown: str, console: str | None = None) -> None:
        print(console if console is not None else markdown)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(markdown + "\n\n")

    def handle(self, message) -> None:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    self.write(block.text.strip(), f"\n[agent] {block.text.strip()}")
                elif isinstance(block, ToolUseBlock):
                    shown = json.dumps(block.input, indent=2)[:1500]
                    self.write(
                        f"**→ {block.name}**\n```json\n{shown}\n```",
                        f"[tool] {block.name} {shown[:300]}",
                    )
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    content = block.content
                    if isinstance(content, list):
                        content = "\n".join(
                            part.get("text", "")
                            for part in content
                            if isinstance(part, dict)
                        )
                    content = (content or "")[:2500]
                    flag = " (error)" if block.is_error else ""
                    self.write(
                        f"**← result{flag}**\n```\n{content}\n```",
                        f"[result{flag}] {content[:400]}",
                    )
        elif isinstance(message, ResultMessage):
            cost = (
                f"${message.total_cost_usd:.2f}"
                if message.total_cost_usd is not None
                else "n/a"
            )
            self.write(
                f"---\nFinished: {message.subtype}, turns={message.num_turns}, cost={cost}"
            )


async def run_agent(args: argparse.Namespace) -> int:
    issue_number = args.issue
    branch = config.branch_name(issue_number)
    run_dir = config.RUNS_DIR / datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"[setup] issue #{issue_number}, branch {branch}, mode={'local-only' if args.local_only else 'dry-run' if args.dry_run else 'live (fork)'}"
    )
    if args.local_only:
        ctx = github_ops.setup_local_only(branch, config.WORK_ROOT)
    else:
        ctx = github_ops.setup_fork(branch, config.WORK_ROOT)
    dry_run = args.dry_run or args.local_only
    print(f"[setup] repo at {ctx.repo_dir}")

    issue = fetch_issue(issue_number)
    if issue["state"] != "open":
        print(f"[warn] issue #{issue_number} is {issue['state']}.")
    contributing = load_contributing(ctx.repo_dir)

    transcript = Transcript(run_dir / "transcript.md")
    scratch = Path(tempfile.mkdtemp(prefix="contrib-agent-"))
    options = ClaudeAgentOptions(
        system_prompt=build_system_prompt(issue_number, issue, contributing, ctx),
        cwd=str(ctx.repo_dir),
        model=args.model,
        mcp_servers={"contrib": build_tool_server(ctx, issue_number, dry_run, run_dir)},
        allowed_tools=[
            "Read",
            "Edit",
            "Write",
            "Glob",
            "Grep",
            "Bash",
            "mcp__contrib__run_checks",
            "mcp__contrib__commit_and_push",
            "mcp__contrib__open_pr",
        ],
        disallowed_tools=["WebFetch", "WebSearch", "Task", "Agent", "NotebookEdit"],
        permission_mode="acceptEdits",
        hooks=build_hooks(ctx.repo_dir, run_dir / "tool_audit.jsonl"),
        env=credential_free_env(scratch),
        setting_sources=[],  # ignore any .claude/ settings or CLAUDE.md in the cloned repo
        max_turns=config.MAX_TURNS,
        max_budget_usd=config.MAX_BUDGET_USD,
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(TASK_PROMPT.format(issue_number=issue_number))
        async for message in client.receive_response():
            transcript.handle(message)

    # Independent final verification, regardless of what the agent claimed.
    final = github_ops.run_checks(ctx.repo_dir)
    diff = github_ops.run(
        ["git", "diff", f"upstream/{config.UPSTREAM_BRANCH}...HEAD"], cwd=ctx.repo_dir
    ).stdout
    (run_dir / "final_checks.txt").write_text(final.text, encoding="utf-8")
    (run_dir / "change.diff").write_text(diff, encoding="utf-8")
    print(f"\n[verify] blocking checks {'PASS' if final.passed else 'FAIL'}")
    print(f"[verify] artifacts in {run_dir}")
    return 0 if final.passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--issue", type=int, default=config.ISSUE_NUMBER)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fork and commit locally, but do not push or open a PR",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="clone upstream read-only; no GitHub account used",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="e.g. claude-opus-5-5 or claude-sonnet-5-5 (default: SDK default)",
    )
    args = parser.parse_args()
    os.environ.setdefault("PYTHONUTF8", "1")  # Windows consoles
    sys.exit(asyncio.run(run_agent(args)))


if __name__ == "__main__":
    main()
