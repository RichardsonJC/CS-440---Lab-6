"""
Deterministic Git/GitHub plumbing: fork, clone, branch, check, commit, push, PR.

Nothing here is decided by the model. Every write goes to the user's fork, and
each function verifies that before acting:
  * the fork's parent must be UPSTREAM_REPO (so we know we're on a fork),
  * the clone's 'upstream' remote has its push URL disabled,
  * pushes go to 'origin' only after confirming origin is the fork,
  * PRs are created with an explicit --repo <fork> (gh otherwise defaults a
    fork's PRs to the parent repo).
"""

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from config import UPSTREAM_BRANCH, UPSTREAM_REPO
from guards import check_editable_path, check_outgoing_text

DISABLED_PUSH_URL = "PUSH_DISABLED_do_not_contribute_upstream"


class GuardError(RuntimeError):
    """Raised when an action would touch upstream or break CONTRIBUTING.md."""


def run(
    command: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command)} failed ({result.returncode}):\n{result.stderr or result.stdout}"
        )
    return result


@dataclass
class ForkContext:
    login: str
    fork_repo: str  # "<login>/Python"
    repo_dir: Path
    branch: str


def require_tools() -> None:
    missing = [name for name in ("git", "gh") if shutil.which(name) is None]
    if missing:
        sys.exit(f"Missing required tools on PATH: {missing}")
    if run(["gh", "auth", "status"], check=False).returncode != 0:
        sys.exit("gh is not logged in. Run: gh auth login")


def github_login() -> str:
    return run(["gh", "api", "user", "--jq", ".login"]).stdout.strip()


def fetch_issue(issue_number: int) -> dict:
    """Read-only: issue title/body/state from upstream."""
    output = run(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            UPSTREAM_REPO,
            "--json",
            "title,body,state,url,labels",
        ]
    ).stdout
    return json.loads(output)


def ensure_fork(login: str) -> str:
    """Create the fork if needed, then prove it really is a fork of upstream."""
    upstream_name = UPSTREAM_REPO.split("/")[1]
    fork_repo = f"{login}/{upstream_name}"
    view = run(
        ["gh", "repo", "view", fork_repo, "--json", "parent,isFork"], check=False
    )
    if view.returncode != 0:
        print(f"[fork] creating {fork_repo}")
        run(
            [
                "gh",
                "repo",
                "fork",
                UPSTREAM_REPO,
                "--clone=false",
                "--default-branch-only",
            ]
        )
        view = run(["gh", "repo", "view", fork_repo, "--json", "parent,isFork"])
    info = json.loads(view.stdout)
    parent = info.get("parent") or {}
    parent_name = f"{parent.get('owner', {}).get('login')}/{parent.get('name')}"
    if not info.get("isFork") or parent_name.lower() != UPSTREAM_REPO.lower():
        raise GuardError(
            f"{fork_repo} exists but is not a fork of {UPSTREAM_REPO} (parent={parent_name})."
        )
    # Bring the fork's master up to date so the PR diff only shows our change.
    # This writes to the fork, never to upstream.
    run(["gh", "repo", "sync", fork_repo, "--branch", UPSTREAM_BRANCH])
    return fork_repo


def clone_fork(fork_repo: str, repo_dir: Path, branch: str) -> None:
    if not (repo_dir / ".git").exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", f"https://github.com/{fork_repo}.git", str(repo_dir)])
    remotes = run(["git", "remote"], cwd=repo_dir).stdout.split()
    if "upstream" not in remotes:
        run(
            [
                "git",
                "remote",
                "add",
                "upstream",
                f"https://github.com/{UPSTREAM_REPO}.git",
            ],
            cwd=repo_dir,
        )
    run(
        ["git", "remote", "set-url", "--push", "upstream", DISABLED_PUSH_URL],
        cwd=repo_dir,
    )
    run(["git", "fetch", "upstream", UPSTREAM_BRANCH], cwd=repo_dir)
    if shutil.which("pre-commit"):
        # CONTRIBUTING.md: "pre-commit install" so hooks run on every commit.
        run(["pre-commit", "install"], cwd=repo_dir, check=False)
    # Make any stray `gh` command in this clone default to the fork.
    run(["gh", "repo", "set-default", fork_repo], cwd=repo_dir)
    existing = run(["git", "branch", "--list", branch], cwd=repo_dir).stdout.strip()
    if existing:
        run(["git", "checkout", branch], cwd=repo_dir)
    else:
        run(
            ["git", "checkout", "-b", branch, f"upstream/{UPSTREAM_BRANCH}"],
            cwd=repo_dir,
        )


def setup_local_only(issue_branch: str, work_root: Path) -> ForkContext:
    """
    Practice mode: no GitHub account needed and nothing is written anywhere but
    disk. Clones upstream read-only with every push URL disabled.
    """
    if shutil.which("git") is None:
        sys.exit("git is not on PATH.")
    repo_dir = work_root / "local-only__Python"
    if not (repo_dir / ".git").exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", f"https://github.com/{UPSTREAM_REPO}.git", str(repo_dir)])
        run(["git", "remote", "rename", "origin", "upstream"], cwd=repo_dir)
    run(
        ["git", "remote", "set-url", "--push", "upstream", DISABLED_PUSH_URL],
        cwd=repo_dir,
    )
    run(["git", "fetch", "upstream", UPSTREAM_BRANCH], cwd=repo_dir)
    if shutil.which("pre-commit"):
        # CONTRIBUTING.md: "pre-commit install" so hooks run on every commit.
        run(["pre-commit", "install"], cwd=repo_dir, check=False)
    existing = run(
        ["git", "branch", "--list", issue_branch], cwd=repo_dir
    ).stdout.strip()
    if existing:
        run(["git", "checkout", issue_branch], cwd=repo_dir)
    else:
        run(
            ["git", "checkout", "-b", issue_branch, f"upstream/{UPSTREAM_BRANCH}"],
            cwd=repo_dir,
        )
    return ForkContext(
        login="local", fork_repo="(local-only)", repo_dir=repo_dir, branch=issue_branch
    )


def setup_fork(issue_branch: str, work_root: Path) -> ForkContext:
    require_tools()
    login = github_login()
    fork_repo = ensure_fork(login)
    repo_dir = work_root / fork_repo.replace("/", "__")
    clone_fork(fork_repo, repo_dir, issue_branch)
    return ForkContext(
        login=login, fork_repo=fork_repo, repo_dir=repo_dir, branch=issue_branch
    )


# ---------------------------------------------------------------- checks


def changed_files(repo_dir: Path) -> list[str]:
    """Files changed on this branch relative to upstream master, plus uncommitted ones."""
    committed = run(
        ["git", "diff", "--name-only", f"upstream/{UPSTREAM_BRANCH}...HEAD"],
        cwd=repo_dir,
    ).stdout
    working = run(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo_dir
    ).stdout
    names = set(committed.split())
    for line in working.splitlines():
        names.add(line[3:].strip().strip('"'))
    return sorted(name for name in names if name)


def _python_for_checks() -> list[str]:
    """CONTRIBUTING.md asks for free-threaded 3.15t+; use it when installed."""
    for candidate in ("python3.15t", "python3.16t"):
        if shutil.which(candidate):
            return [candidate]
    if sys.platform == "win32" and shutil.which("py"):
        probe = run(["py", "-3.15t", "--version"], check=False)
        if probe.returncode == 0:
            return ["py", "-3.15t"]
    return [sys.executable]


def _tool_command(name: str) -> list[str]:
    """Prefer the standalone executable; fall back to the module in this venv."""
    return [name] if shutil.which(name) else [sys.executable, "-m", name]


@dataclass
class CheckReport:
    passed: bool
    text: str


def run_checks(repo_dir: Path) -> CheckReport:
    """
    The checks CONTRIBUTING.md names: ruff check (blocking), ruff format,
    doctests (blocking), ty (informational), pre-commit on changed files.
    """
    files = changed_files(repo_dir)
    py_files = [
        name for name in files if name.endswith(".py") and (repo_dir / name).exists()
    ]
    lines = [f"Changed files: {files or 'none'}"]
    blocking_ok = True

    for name in files:
        problem = check_editable_path(str(repo_dir / name), repo_dir)
        if problem and (repo_dir / name).exists():
            blocking_ok = False
            lines.append(f"[FAIL] policy: {problem}")

    python = _python_for_checks()
    version = run([*python, "-VV"], check=False).stdout.strip()
    lines.append(f"Python used for doctests: {version}")
    if "free-threading" not in version:
        lines.append(
            "[NOTE] Free-threaded 3.15t not found locally; CI will run the official one."
        )

    ruff = _tool_command("ruff")
    ty = _tool_command("ty")
    steps: list[tuple[str, list[str], bool]] = []
    if py_files:
        steps.append(("ruff check", [*ruff, "check", *py_files], True))
        steps.append(
            ("ruff format --check", [*ruff, "format", "--check", *py_files], True)
        )
        for name in py_files:
            steps.append(
                (f"doctest {name}", [*python, "-m", "doctest", "-v", name], True)
            )
        steps.append(("ty check (informational)", [*ty, "check", *py_files], False))
    if files and shutil.which("pre-commit"):
        steps.append(
            (
                "pre-commit",
                ["pre-commit", "run", "--show-diff-on-failure", "--files", *files],
                True,
            )
        )
    elif files:
        lines.append(
            "[NOTE] pre-commit not installed; skipped (pip install pre-commit)."
        )

    for label, command, blocking in steps:
        result = run(command, cwd=repo_dir, check=False)
        status = "PASS" if result.returncode == 0 else ("FAIL" if blocking else "WARN")
        if result.returncode != 0 and blocking:
            blocking_ok = False
        output = (result.stdout + result.stderr).strip()
        if label.startswith("doctest") and result.returncode == 0:
            output = output.splitlines()[-1] if output else ""  # just the summary line
        lines.append(f"[{status}] {label}\n{output[-3000:]}")

    if not py_files:
        blocking_ok = False
        lines.append("[FAIL] No Python changes found.")
    return CheckReport(passed=blocking_ok, text="\n\n".join(lines))


# ---------------------------------------------------------------- writes (fork only)


def _assert_origin_is_fork(ctx: ForkContext) -> None:
    origin = run(
        ["git", "remote", "get-url", "--push", "origin"], cwd=ctx.repo_dir
    ).stdout.strip()
    normalized = origin.lower().removesuffix("/").removesuffix(".git")
    fork = ctx.fork_repo.lower()
    if not normalized.endswith((f"github.com/{fork}", f"github.com:{fork}")):
        raise GuardError(f"origin push URL {origin!r} is not the fork {ctx.fork_repo}.")


def commit_and_push(ctx: ForkContext, message: str, dry_run: bool) -> str:
    problem = check_outgoing_text(message)
    if problem:
        raise GuardError(problem)
    for name in changed_files(ctx.repo_dir):
        path_problem = check_editable_path(str(ctx.repo_dir / name), ctx.repo_dir)
        if path_problem and (ctx.repo_dir / name).exists():
            raise GuardError(f"Refusing to commit {name}: {path_problem}")
    run(["git", "add", "--all"], cwd=ctx.repo_dir)
    if (
        run(
            ["git", "diff", "--cached", "--quiet"], cwd=ctx.repo_dir, check=False
        ).returncode
        == 0
    ):
        return "Nothing to commit."
    # pre-commit hooks may reformat; if so, re-add and commit once more.
    first = run(["git", "commit", "-m", message], cwd=ctx.repo_dir, check=False)
    if first.returncode != 0:
        run(["git", "add", "--all"], cwd=ctx.repo_dir)
        run(["git", "commit", "-m", message], cwd=ctx.repo_dir)
    sha = run(["git", "rev-parse", "--short", "HEAD"], cwd=ctx.repo_dir).stdout.strip()
    if dry_run:
        return f"Committed {sha} locally (dry run: not pushed)."
    _assert_origin_is_fork(ctx)
    run(["git", "push", "--set-upstream", "origin", ctx.branch], cwd=ctx.repo_dir)
    return f"Committed {sha} and pushed to {ctx.fork_repo}:{ctx.branch}."


def open_pr(
    ctx: ForkContext,
    title: str,
    body: str,
    issue_number: int,
    dry_run: bool,
    body_out: Path,
) -> str:
    for text in (title, body):
        problem = check_outgoing_text(text)
        if problem:
            raise GuardError(problem)
    if f"Fixes #{issue_number}" not in body:
        raise GuardError(
            f"CONTRIBUTING.md requires 'Fixes #{issue_number}' in the PR description."
        )
    report = run_checks(ctx.repo_dir)
    if not report.passed:
        raise GuardError(
            "Checks are failing; fix them before opening a PR.\n\n" + report.text
        )
    unpushed = run(["git", "status", "--porcelain"], cwd=ctx.repo_dir).stdout.strip()
    if unpushed:
        raise GuardError("Uncommitted changes remain; call commit_and_push first.")

    body_out.parent.mkdir(parents=True, exist_ok=True)
    body_out.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    if dry_run:
        return f"Dry run: PR not created. Title/body saved to {body_out}."

    _assert_origin_is_fork(ctx)
    existing = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            ctx.fork_repo,
            "--head",
            ctx.branch,
            "--state",
            "open",
            "--json",
            "url",
            "--jq",
            ".[0].url",
        ]
    ).stdout.strip()
    if existing:
        run(
            [
                "gh",
                "pr",
                "edit",
                existing,
                "--repo",
                ctx.fork_repo,
                "--title",
                title,
                "--body-file",
                str(body_out),
            ]
        )
        return f"Updated existing PR on the fork: {existing}"
    url = (
        run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                ctx.fork_repo,  # explicit: gh defaults fork PRs to the parent
                "--base",
                UPSTREAM_BRANCH,
                "--head",
                f"{ctx.login}:{ctx.branch}",
                "--title",
                title,
                "--body-file",
                str(body_out),
            ]
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    if f"github.com/{ctx.fork_repo}/pull/".lower() not in url.lower():
        raise GuardError(
            f"PR landed somewhere unexpected: {url}. Close it immediately."
        )
    return f"Opened PR on the fork: {url}"
