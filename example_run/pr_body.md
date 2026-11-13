# Raise ValueError when required chars exceed password length

### Describe your change

`alternative_password_generator` subtracted `len(chars_incl)` from the requested length, so when the required characters were longer than the requested length `i` became negative. `i // 3` went negative while `i % 3` stayed positive, so the function silently returned a password longer than requested. It now raises `ValueError` when `i < len(chars_incl)`, and doctests cover normal lengths, the `length == len(chars_incl)` edge case and the erroneous inputs from the issue.

Fixes #15633

### Checklist

- [x] ruff check passes
- [x] ruff format applied
- [x] Doctests added for valid and invalid input
- [x] Type hints on parameters and return values
- [x] No README.md / DIRECTORY.md edits
- [x] Single file changed (`other/password.py`)
- [ ] This is my own work. This change was produced by an AI agent under the author's direction.

### Verification

Before:
```
len(alternative_password_generator("abcdefgh", 5))  -> 8
len(alternative_password_generator("abcd", 3))      -> 5
```

After:
```
alternative_password_generator("abcdefgh", 5) -> ValueError: The password length must be at least the number of required chars
alternative_password_generator("abcd", 3)      -> ValueError: The password length must be at least the number of required chars
len(alternative_password_generator("abc", 10)) -> 10
len(alternative_password_generator("abcd", 4)) -> 4
```

🤖 Generated with [Claude Code](https://claude.com/claude-code)
