"""Evidence-based impact and recovery assessments for unreadable archive entries."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .analyzer import RepositoryAnalysis

IMPACT_PROBLEM_TYPES = {
    "CRC failure",
    "Unreadable member",
    "Undecodable text",
    "Unsupported compression",
    "Encrypted entry",
}


def _normalized(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/").casefold()


def _reference_pattern(path: str) -> re.Pattern[str]:
    parts = re.split(r"[/\\]", path)
    body = r"[/\\]".join(re.escape(part) for part in parts if part)
    return re.compile(rf"(?<![A-Za-z0-9_.-]){body}(?![A-Za-z0-9_.-])", re.IGNORECASE)


def _recovery(problem_type: str) -> str:
    if problem_type == "CRC failure":
        return "CRC mismatch: obtain a clean copy of this file from a trusted source, recreate the ZIP archive, and upload it again."
    if problem_type == "Unsupported compression":
        return "Recreate the ZIP using a supported compression method, then upload the new archive."
    if problem_type == "Undecodable text":
        return "Verify the file's intended text encoding and regenerate it using an encoding the analyzer supports (UTF-8)."
    if problem_type == "Encrypted entry":
        return "Create a new archive containing the file without ZIP encryption, then upload that archive."
    return "Obtain a clean copy of this file from a trusted source, recreate the ZIP archive, and upload it again."


def assess_integrity_impact(analysis: RepositoryAnalysis) -> tuple[dict[str, object], ...]:
    """Assess unreadable entries only from archive metadata, claims, and decoded text."""
    readable_files = dict(analysis.decoded_files)
    results: list[dict[str, object]] = []

    for issue in analysis.integrity_issues:
        if issue.problem_type not in IMPACT_PROBLEM_TYPES:
            continue
        metadata = next(
            (item for item in analysis.file_metadata if item.path == issue.path),
            None,
        )
        if metadata is None or metadata.safe_path is None:
            continue
        safe_path = metadata.safe_path

        confirmed = {
            "CRC failure": "The ZIP member failed its CRC check; its contents are not trusted.",
            "Unreadable member": "The ZIP member could not be read; its contents are not trusted.",
            "Undecodable text": "The member bytes were read, but UTF-8 decoding failed, so its text was not inspected.",
            "Unsupported compression": "The member uses unsupported compression and could not be inspected; this does not establish that its contents are corrupt.",
            "Encrypted entry": "The encrypted member could not be inspected; this does not establish that its contents are corrupt.",
        }[issue.problem_type]

        references: list[dict[str, object]] = []
        matcher = _reference_pattern(safe_path)
        for source_path, content in readable_files.items():
            if source_path == safe_path:
                continue
            for line_number, line in enumerate(content.splitlines(), 1):
                if matcher.search(line):
                    references.append({
                        "path": source_path,
                        "line": line_number,
                        "text": line.strip(),
                    })

        affected_checks: list[dict[str, object]] = []
        normalized_target = _normalized(safe_path)
        basename_target = PurePosixPath(safe_path).name.casefold()
        for claim in analysis.claims:
            claim_path = _normalized(claim.claim.strip().strip("`'\""))
            evidence_text = claim.evidence.replace("\\", "/").casefold()
            direct_path_claim = (
                claim.kind == "File path"
                and (
                    claim_path == normalized_target
                    or ("/" not in claim_path and claim_path == basename_target)
                )
            )
            evidence_dependency = normalized_target in evidence_text
            command_dependency = (
                claim.kind == "Installation command"
                and basename_target in claim.claim.replace("\\", "/").casefold()
            )
            if direct_path_claim or evidence_dependency or command_dependency:
                affected_checks.append({
                    "kind": claim.kind,
                    "claim": claim.claim,
                    "source": claim.source_document,
                    "line": claim.line,
                    "status": claim.status,
                    "evidence": claim.evidence,
                })

        if references:
            impact = (
                "The listed readable repository files explicitly reference this member. "
                "Those components may rely on content that could not be trusted; runtime impact is undetermined."
            )
        elif affected_checks:
            impact = "A recorded verification check depends on this path; the broader runtime impact is undetermined."
        else:
            impact = "Impact is undetermined. No dependency or reference was found in the readable evidence; absence of a discovered reference does not prove the file is unused."

        limitations = (
            "The original contents and runtime behavior of this member are unavailable or untrusted. "
            "Reference scanning covers only readable decoded repository text; no reference found is not proof of non-use."
        )
        results.append({
            "path": safe_path,
            "error_type": issue.problem_type,
            "details": issue.details,
            "what_is_confirmed": confirmed,
            "potential_impact": impact,
            "affected_checks": affected_checks,
            "references": references,
            "recovery": _recovery(issue.problem_type),
            "limitations": limitations,
        })
    return tuple(results)
