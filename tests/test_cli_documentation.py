"""Keep the user-facing CLI references in sync with argparse."""

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _long_cli_options() -> set[str]:
    tree = ast.parse((ROOT / "book_maker/cli.py").read_text(encoding="utf-8"))
    options: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            continue
        options.update(
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value.startswith("--")
        )
    return options


def _documented_options(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    # Avoid treating --batch_size as documentation for --batch.
    return {
        option
        for option in _long_cli_options()
        if re.search(re.escape(option) + r"(?![\w-])", text)
    }


def test_cli_references_mention_every_long_option():
    expected = _long_cli_options()
    for name in ("README.md", "README-CN.md", "docs/cmd.md"):
        documented = _documented_options(ROOT / name)
        assert (
            documented == expected
        ), f"{name} is missing CLI options: {sorted(expected - documented)}"


def test_help_renders():
    """`--help` must survive argparse's own %-interpolation.

    argparse runs every help string through `%` formatting, so a literal
    percent sign in one of them ("90% of it") is read as a conversion and
    raises TypeError — taking down `--help` for the whole CLI, not just the
    flag that owns the string. Nothing else exercises this: the help text is
    never formatted until someone asks for it.
    """
    proc = subprocess.run(
        [sys.executable, "make_book.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--context-compact-at" in proc.stdout
    assert "90% of" in proc.stdout.replace("\n", " ").replace("  ", " ")
