"""Build conservative README correction previews from analyzer evidence."""

import re

from .analyzer import ClaimResult

BACKTICKED_SUGGESTION = re.compile(r"`([^`]+)`")


def build_readme_preview(readme: str, claim: ClaimResult) -> str | None:
    if claim.status != "Failed" or not claim.correction:
        return None

    suggestion = BACKTICKED_SUGGESTION.search(claim.correction)
    if suggestion is None:
        return None

    replacement = suggestion.group(1)
    lines = readme.splitlines(keepends=True)
    line_index = claim.line - 1
    if line_index < 0 or line_index >= len(lines) or replacement == claim.claim:
        return None

    line = lines[line_index]
    if line.count(claim.claim) != 1:
        return None

    lines[line_index] = line.replace(claim.claim, replacement, 1)
    return "".join(lines)
