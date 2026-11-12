# Raise ValueError when required chars exceed password length

### Describe your change

`alternative_password_generator(chars_incl, i)` returned a password longer than the requested length `i` when `len(chars_incl) > i`. Subtracting made `i` negative, so `quotient` went negative while `remainder` stayed positive, and extra characters got added. The function now raises `ValueError` when the requested length is shorter than the required characters. I also added a docstring with doctests for normal lengths, the edge case `i == len(chars_incl)`, and the failing inputs from the issue.

Fixes #15633

### Checklist

- [x] `ruff check` passes and the file is formatted with `ruff format`.
- [x] Doctests added for valid and invalid input.
- [x] All function parameters and return values have type hints.
- [x] README.md and DIRECTORY.md are not edited.
- [x] Only one file changed (`other/password.py`).
- [x] This contribution is my own work.

### Verification

Before:
```
>>> len(alternative_password_generator("abcdefgh", 5))  # asked for 5
8
>>> len(alternative_password_generator("abcd", 3))      # asked for 3
5
```

After:
```
>>> len(alternative_password_generator("abcd", 4))
4
>>> len(alternative_password_generator("ab", 10))
10
>>> alternative_password_generator("abcdefgh", 5)
ValueError: Password length 5 is shorter than the 8 required characters
>>> alternative_password_generator("abcd", 3)
ValueError: Password length 3 is shorter than the 4 required characters
```

🤖 Generated with [Claude Code](https://claude.com/claude-code)
