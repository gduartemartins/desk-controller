"""Omarchy's menu dialogs (`omarchy-menu-select` / `omarchy-menu-input`)."""

import subprocess

WIDTH = "420"


def select(prompt: str, options: list[tuple[str, ...]]) -> str | None:
    """Pick one of `(glyph, label[, subtext])` options. Returns the label, or None if dismissed."""
    rows = ["\t".join(option) for option in options]
    result = subprocess.run(
        ["omarchy-menu-select", prompt, *rows, "--", "--width", WIDTH],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # An option with a subtext comes back as "label<TAB>subtext".
    return result.stdout.rstrip("\n").split("\t")[0]


def ask(prompt: str) -> str | None:
    """Ask for a line of text. Returns None if dismissed or empty."""
    result = subprocess.run(
        ["omarchy-menu-input", prompt, "--width", WIDTH], capture_output=True, text=True, check=False
    )
    text = result.stdout.strip()
    return text if result.returncode == 0 and text else None
