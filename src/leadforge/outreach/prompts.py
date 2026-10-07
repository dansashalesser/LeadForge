"""Versioned prompt files (prefs: prompts live in files, never inline).

A prompt is ``prompt_files/<name>_v<N>.txt``; its version is the file stem, so a changed
wording is a new file and every stored plan or Message names the exact text it came
from. Placeholders are plain ``{name}`` markers filled with trusted values only (a
config list, never a Lead's or an operator's text: that goes in a delimited block).
"""

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PROMPTS_DIR", "Prompt", "delimit", "load_prompt"]

PROMPTS_DIR = Path(__file__).resolve().parent / "prompt_files"
_NAME = re.compile(r"[a-z][a-z_]*_v[0-9]+")


@dataclass(frozen=True)
class Prompt:
    version: str
    text: str

    def fill(self, **trusted: str) -> str:
        """The text with each ``{name}`` marker replaced by a trusted value."""
        out = self.text
        for name, value in trusted.items():
            marker = "{" + name + "}"
            if marker not in out:
                raise KeyError(f"prompt {self.version} has no {marker} marker")
            out = out.replace(marker, value)
        return out


def load_prompt(version: str, *, directory: Path = PROMPTS_DIR) -> Prompt:
    """The prompt file ``<version>.txt``; an unknown or malformed name is an error."""
    if not _NAME.fullmatch(version):
        raise ValueError(f"not a prompt version: {version!r}")
    return Prompt(version, (directory / f"{version}.txt").read_text(encoding="utf-8"))


def delimit(tag: str, untrusted: str) -> str:
    """Untrusted text inside ``<tag>`` ... ``</tag>``, with its angle brackets escaped
    so the text cannot close the block or open another."""
    safe = untrusted.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<{tag}>\n{safe}\n</{tag}>"
