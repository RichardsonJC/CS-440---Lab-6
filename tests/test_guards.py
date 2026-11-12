import doctest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import guards


def test_doctests() -> None:
    assert doctest.testmod(guards).failed == 0


def test_blocks_upstream_writes() -> None:
    blocked = [
        "git push origin fix/issue-15633",
        "gh issue comment 15633 --repo TheAlgorithms/Python",
        "git remote set-url --push upstream https://github.com/TheAlgorithms/Python",
        "curl -X POST https://api.github.com/repos/TheAlgorithms/Python/pulls",
        "python3 -m doctest other/password.py; git push",
        "find . -name '*.py' -exec rm {} +",
    ]
    for command in blocked:
        assert guards.check_bash_command(command), command


def test_allows_normal_work() -> None:
    allowed = [
        "ruff check other/password.py",
        "ruff format other/password.py",
        "python3 -m doctest -v other/password.py",
        "python3 -c \"from other.password import alternative_password_generator as g; print(len(g('abcd', 3)))\"",
        "git diff",
        "pre-commit run --files other/password.py",
    ]
    for command in allowed:
        assert guards.check_bash_command(command) is None, command


def test_outgoing_text() -> None:
    assert guards.check_outgoing_text("Fixes #15633\n\nRaise ValueError") is None
    assert guards.check_outgoing_text("Closes TheAlgorithms/Python#15633")
    assert guards.check_outgoing_text(
        "https://github.com/TheAlgorithms/Python/issues/15633"
    )
