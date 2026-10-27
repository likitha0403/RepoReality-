"""Safe ZIP inspection and deterministic README claim verification."""

from __future__ import annotations

import json
import lzma
import re
import stat
import tomllib
import zipfile
import zlib
from dataclasses import asdict, dataclass
from difflib import get_close_matches
from io import BytesIO
from pathlib import PurePosixPath
from typing import Iterable

MAX_ARCHIVE_FILES = 5_000
MAX_COMPRESSED_ARCHIVE_SIZE = 25 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
CONFIG_EXTENSIONS = {".cfg", ".env", ".ini", ".json", ".properties", ".toml", ".xml", ".yaml", ".yml"}
IMAGE_EXTENSIONS = {".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
TEXT_FILE_EXTENSIONS = {".csv", ".log", ".rst", ".txt"}
SOURCE_EXTENSIONS = {
    ".c", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js", ".jsx",
    ".kt", ".mjs", ".php", ".rb", ".rs", ".sh", ".sql", ".swift", ".ts", ".tsx",
}
TEXT_EXTENSIONS = {
    ".c", ".cfg", ".cpp", ".cs", ".css", ".dockerfile", ".env", ".go",
    ".h", ".hpp", ".html", ".ini", ".java", ".js", ".json", ".jsx",
    ".kt", ".md", ".mjs", ".php", ".properties", ".py", ".rb", ".rs",
    ".rst", ".sh", ".sql", ".swift", ".toml", ".ts", ".tsx", ".txt", ".xml",
    ".yaml", ".yml",
}
MANIFEST_NAMES = {
    "requirements.txt", "pyproject.toml", "package.json", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "go.mod", "cargo.toml", "composer.json",
    "gemfile", "pom.xml",
}
PATH_PATTERN = re.compile(
    r"(?<![\w])(?:\.{0,2}[\\/])?(?:[\w.@+-]+[\\/])*[\w.@+-]+\.[A-Za-z][\w-]*(?![\w])",
    re.IGNORECASE,
)
EXPLICIT_FILE_CONTEXT = re.compile(
    r"\b(?:file|path)\s*(?:named|called|:)?\s*[`'\"]?\s*$"
    r"|\b(?:the|this|following)\s+(?:source\s+)?file\s*:\s*[`'\"]?\s*$",
    re.IGNORECASE,
)
AMBIGUOUS_REFERENCE_CONTEXT = re.compile(
    r"\b(?:see|open|edit|refer to|look at)\s*$",
    re.IGNORECASE,
)
MARKDOWN_LINK = re.compile(r"!?\[[^]]*\]\(([^)]+)\)")
CONFIG_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)(?![A-Za-z0-9])")
RUNTIME_PATTERN = re.compile(
    r"\b(Python|Node(?:\.js)?|Go|Rust)\s+(?:version\s+)?"
    r"(\d+(?:\.\d+){0,2})(?:\s*(?:or later|or newer|and later|\+))?",
    re.IGNORECASE,
)
COMMAND_PATTERN = re.compile(
    r"(?i)(?:^|\s)(pip(?:3)?\s+install\b[^\n`]*|"
    r"(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+pip\s+install\b[^\n`]*|"
    r"(?:npm|yarn|pnpm)\s+(?:install|i)\b[^\n`]*|"
    r"poetry\s+install\b[^\n`]*|uv\s+sync\b[^\n`]*|"
    r"go\s+mod\s+download\b[^\n`]*|cargo\s+build\b[^\n`]*|"
    r"composer\s+install\b[^\n`]*)"
)
LOCAL_VENV_CREATE_PATTERN = re.compile(
    r"(?i)\b(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+venv\s+([^\s`\"']+)"
)


class RepoArchiveError(ValueError):
    """An uploaded archive is invalid or exceeds safe inspection limits."""


@dataclass(frozen=True)
class ClaimResult:
    kind: str
    claim: str
    line: int
    status: str
    evidence: str
    correction: str = ""
    rule: str = ""
    reason: str = ""
    source_document: str = "README.md"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["source"] = self.source_document
        if self.status in {"Failed", "Unverified"}:
            result["guidance"] = self.guidance()
        return result

    def guidance(self) -> dict[str, str]:
        local_venv = (
            ".venv" in self.claim.casefold()
            and "generated locally" in self.evidence.casefold()
        )
        suggested_fix = (
            "No README change is needed because `.venv` is created locally and is not expected in the archive."
            if local_venv
            else self.correction
            or "Inspect the README claim against the available archive evidence and confirm the intended behavior manually."
        )
        return {
            "README says": f"{self.claim} (README line {self.line})",
            "Rule checked": (
                "Check whether the README's repository-relative path or other documented requirement "
                "is supported by the uploaded archive evidence."
            ),
            "What we found": self.evidence or "No supporting archive evidence was available.",
            "Why it is flagged": {
                "Failed": f"The claim does not match the available archive evidence: {self.evidence}",
                "Unverified": (
                    "The evidence is insufficient to confirm the claim: "
                    f"{self.evidence or 'no supporting archive evidence was recorded.'}"
                ),
                "Verified": f"The available archive evidence supports the claim: {self.evidence}",
            }.get(self.status, f"No guidance is defined for status {self.status}."),
            "Type": (
                "Documentation error (Failed)"
                if self.status == "Failed"
                else "Needs human review (Unverified)"
                if self.status == "Unverified"
                else "Verified"
            ),
            "Suggested fix": suggested_fix,
        }


@dataclass(frozen=True)
class RepositoryAnalysis:
    repository_name: str
    files: tuple[str, ...]
    readme_path: str | None
    claims: tuple[ClaimResult, ...]
    file_metadata: tuple[ArchiveFileMetadata, ...] = ()
    readme_content: str | None = None
    integrity_issues: tuple[ArchiveIntegrityIssue, ...] = ()
    inspection_complete: bool = True
    unavailable_paths: tuple[str, ...] = ()
    documents_checked: tuple[DocumentCheck, ...] = ()
    document_contents: tuple[tuple[str, str], ...] = ()
    decoded_files: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DocumentCheck:
    path: str
    status: str
    claim_count: int
    reason: str = ""


@dataclass(frozen=True)
class ArchiveFileMetadata:
    path: str
    size_bytes: int
    file_type: str
    entry_type: str = "File"
    inspection_status: str = "Not inspected"
    inspection_details: str = ""
    safe_path: str | None = None


@dataclass(frozen=True)
class ArchiveIntegrityIssue:
    path: str
    problem_type: str
    details: str
    category: str


def _file_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    filename = PurePosixPath(path).name.lower()
    if suffix == ".md":
        return "Markdown"
    if suffix == ".py":
        return "Python"
    if suffix in CONFIG_EXTENSIONS or filename in MANIFEST_NAMES:
        return "Config"
    if suffix in IMAGE_EXTENSIONS:
        return "Image"
    if suffix in TEXT_FILE_EXTENSIONS:
        return "Text"
    if suffix in SOURCE_EXTENSIONS:
        return "Source"
    return "Other"


def _safe_path(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"..", "."} for part in normalized.split("/"))
    ):
        raise RepoArchiveError(f"Unsafe archive path: {name}")
    return path.as_posix()


def _read_repository_archive(
    archive_bytes: bytes,
) -> tuple[
    dict[str, str],
    tuple[str, ...],
    tuple[ArchiveFileMetadata, ...],
    tuple[ArchiveIntegrityIssue, ...],
    bool,
    tuple[str, ...],
]:
    """Read bounded archive paths and text files without extracting to disk."""
    if len(archive_bytes) > MAX_COMPRESSED_ARCHIVE_SIZE:
        raise RepoArchiveError(
            f"Compressed archive exceeds the {MAX_COMPRESSED_ARCHIVE_SIZE // (1024 * 1024)} MB limit."
        )
    try:
        archive = zipfile.ZipFile(BytesIO(archive_bytes))
    except (zipfile.BadZipFile, OSError, NotImplementedError) as exc:
        raise RepoArchiveError("This file is not a valid ZIP archive.") from exc

    try:
        entries = archive.infolist()
        issues: list[ArchiveIntegrityIssue] = []
        issue_by_index: dict[int, list[ArchiveIntegrityIssue]] = {}
        records: list[dict[str, object]] = []

        def add_issue(
            index: int,
            path: str,
            problem_type: str,
            details: str,
            category: str,
        ) -> None:
            issue = ArchiveIntegrityIssue(path, problem_type, details, category)
            issues.append(issue)
            issue_by_index.setdefault(index, []).append(issue)

        if len(entries) > MAX_ARCHIVE_FILES:
            raise RepoArchiveError(f"Archive contains more than {MAX_ARCHIVE_FILES:,} entries.")

        metadata_files = {".ds_store", "thumbs.db", "desktop.ini"}
        valid_file_paths: list[str] = []
        has_root_files = False
        for index, entry in enumerate(entries):
            is_directory = entry.is_dir()
            display_path = entry.filename
            safe_path: str | None = None
            try:
                safe_path = _safe_path(entry.filename)
                display_path = safe_path + ("/" if is_directory else "")
            except RepoArchiveError as exc:
                add_issue(index, entry.filename, "Unsafe path", str(exc), "security")

            if safe_path is not None and not is_directory:
                valid_file_paths.append(safe_path)
                if len(PurePosixPath(safe_path).parts) == 1 and PurePosixPath(safe_path).name.lower() not in metadata_files:
                    has_root_files = True
            records.append({
                "index": index,
                "entry": entry,
                "display_path": display_path,
                "safe_path": safe_path,
                "entry_type": "Folder" if is_directory else "File",
            })

        project_roots = {
            PurePosixPath(path).parts[0]
            for path in valid_file_paths
            if len(PurePosixPath(path).parts) > 1
            and PurePosixPath(path).parts[0].lower() not in metadata_files
        }
        strip_root = next(iter(project_roots)) if len(project_roots) == 1 and not has_root_files else None
        for record in records:
            safe_path = record["safe_path"]
            if safe_path is None:
                continue
            is_directory = record["entry_type"] == "Folder"
            entry_path = str(safe_path)
            if strip_root and entry_path == strip_root and is_directory:
                record["display_path"] = entry_path + "/"
                continue
            if strip_root and entry_path.startswith(strip_root + "/"):
                normalized = entry_path[len(strip_root) + 1:]
                record["safe_path"] = normalized
                record["display_path"] = normalized + ("/" if is_directory else "")

        collision_groups: dict[str, list[dict[str, object]]] = {}
        for record in records:
            safe_path = record["safe_path"]
            if safe_path is not None:
                collision_groups.setdefault(str(safe_path).casefold(), []).append(record)
        for group in collision_groups.values():
            if len(group) > 1:
                colliding_paths = ", ".join(str(item["display_path"]) for item in group)
                for record in group:
                    add_issue(
                        int(record["index"]),
                        str(record["display_path"]),
                        "Duplicate or case-colliding path",
                        f"Normalized archive path collides with: {colliding_paths}. The ambiguous entries are excluded from verification.",
                        "security",
                    )

        budget_used = 0
        for record in records:
            index = int(record["index"])
            entry = record["entry"]
            path = str(record["display_path"])
            safe_path = record["safe_path"]
            if index >= MAX_ARCHIVE_FILES:
                raise RepoArchiveError(f"Archive contains more than {MAX_ARCHIVE_FILES:,} entries.")
            if safe_path is None:
                continue
            if any(issue.problem_type == "Duplicate or case-colliding path" for issue in issue_by_index.get(index, [])):
                continue
            if entry.flag_bits & 0x1:
                add_issue(index, path, "Encrypted entry", "Encrypted ZIP members cannot be inspected.", "security")
                continue
            if stat.S_ISLNK(entry.external_attr >> 16):
                add_issue(index, path, "Symbolic link", "Symbolic-link entries are not followed or inspected.", "security")
                continue
            if record["entry_type"] == "Folder":
                continue
            if entry.file_size > MAX_FILE_SIZE:
                raise RepoArchiveError(
                    f"File exceeds the {MAX_FILE_SIZE // (1024 * 1024)} MB limit: {path}"
                )
            if entry.file_size and entry.file_size / max(entry.compress_size, 1) > MAX_COMPRESSION_RATIO:
                raise RepoArchiveError(
                    f"File exceeds the maximum compression ratio of {MAX_COMPRESSION_RATIO}: {path}"
                )
            if budget_used + entry.file_size > MAX_TOTAL_UNCOMPRESSED:
                raise RepoArchiveError(
                    "Archive exceeds the 50 MB uncompressed inspection limit."
                )
            budget_used += entry.file_size

        metadata_by_index: dict[int, ArchiveFileMetadata] = {}
        for record in records:
            index = int(record["index"])
            entry = record["entry"]
            path = str(record["display_path"])
            safe_path = record["safe_path"]
            entry_type = str(record["entry_type"])
            entry_issues = issue_by_index.get(index, [])
            if entry_type == "Folder" and not entry_issues:
                status, details = "Directory entry", "Directory metadata recorded; directory content is not read."
            elif entry_issues:
                issue = entry_issues[0]
                status, details = issue.problem_type, issue.details
            else:
                status, details = "Not inspected", ""
            metadata_by_index[index] = ArchiveFileMetadata(
                path=path,
                size_bytes=entry.file_size,
                file_type="Folder" if entry_type == "Folder" else _file_type(str(safe_path or path)),
                entry_type=entry_type,
                inspection_status=status,
                inspection_details=details,
                safe_path=str(safe_path) if safe_path is not None else None,
            )

        files: dict[str, str] = {}
        available_paths: list[str] = []
        for record in records:
            index = int(record["index"])
            entry = record["entry"]
            safe_path = record["safe_path"]
            if safe_path is None or record["entry_type"] == "Folder":
                continue
            metadata = metadata_by_index[index]
            if metadata.inspection_status != "Not inspected":
                continue
            path = str(safe_path)
            try:
                raw_content = archive.read(entry)
            except NotImplementedError as exc:
                add_issue(index, metadata.path, "Unsupported compression", str(exc) or "The ZIP compression method is not supported.", "security")
                metadata_by_index[index] = ArchiveFileMetadata(
                    **{**metadata.__dict__, "inspection_status": "Unsupported compression", "inspection_details": str(exc) or "The ZIP compression method is not supported."}
                )
                continue
            except (
                EOFError,
                lzma.LZMAError,
                OSError,
                RuntimeError,
                zipfile.BadZipFile,
                zlib.error,
            ) as exc:
                is_crc = isinstance(exc, zipfile.BadZipFile) and "crc" in str(exc).casefold()
                problem = "CRC failure" if is_crc else "Unreadable member"
                details = str(exc) or "The ZIP member could not be read; its contents are not trusted."
                add_issue(index, metadata.path, problem, details, "corrupt")
                metadata_by_index[index] = ArchiveFileMetadata(
                    **{**metadata.__dict__, "inspection_status": problem, "inspection_details": details}
                )
                continue

            if (
                PurePosixPath(path).suffix.lower() not in TEXT_EXTENSIONS
                and PurePosixPath(path).name.lower() not in MANIFEST_NAMES
            ):
                available_paths.append(path)
                metadata_by_index[index] = ArchiveFileMetadata(
                    **{**metadata.__dict__, "inspection_status": "Inspected (binary)", "inspection_details": "Member data read successfully; text decoding was not applicable."}
                )
                continue
            try:
                files[path] = raw_content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                details = str(exc)
                add_issue(index, metadata.path, "Undecodable text", details, "unreadable")
                metadata_by_index[index] = ArchiveFileMetadata(
                    **{**metadata.__dict__, "inspection_status": "Undecodable text", "inspection_details": details}
                )
                continue
            available_paths.append(path)
            metadata_by_index[index] = ArchiveFileMetadata(
                **{**metadata.__dict__, "inspection_status": "Inspected (UTF-8)", "inspection_details": "Member data and text decoding completed successfully."}
            )
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
        raise RepoArchiveError("The ZIP archive could not be read safely.") from exc
    finally:
        archive.close()
    metadata = tuple(
        metadata_by_index[index]
        for index in sorted(metadata_by_index, key=lambda item: (metadata_by_index[item].path.casefold(), item))
    )
    paths = tuple(
        str(record["safe_path"])
        for record in records
        if record["safe_path"] is not None and record["entry_type"] == "File"
    )
    return files, paths, metadata, tuple(issues), not issues, tuple(available_paths)


def read_repository_archive(archive_bytes: bytes) -> dict[str, str]:
    """Read bounded text files from ZIP bytes without extracting to disk."""
    files, _, _, _, _, _ = _read_repository_archive(archive_bytes)
    return files


def _find_readme(files: dict[str, str]) -> str | None:
    root_readmes = [path for path in files if "/" not in path and PurePosixPath(path).name.lower() == "readme.md"]
    if root_readmes:
        return sorted(root_readmes)[0]
    readmes = [path for path in files if PurePosixPath(path).name.lower() == "readme.md"]
    return sorted(readmes, key=lambda path: (path.count("/"), path.lower()))[0] if readmes else None


def _dependency_names(files: dict[str, str]) -> tuple[dict[str, str], bool]:
    dependencies: dict[str, str] = {}
    supported = False

    def record(name: str, path: str) -> None:
        dependencies.setdefault(name.lower().replace("_", "-"), path)

    for path, content in files.items():
        name = PurePosixPath(path).name.lower()
        try:
            if name == "requirements.txt":
                supported = True
                for line in content.splitlines():
                    match = re.match(r"\s*([A-Za-z0-9_.-]+)\s*(?:[<>=!~;\[]|$)", line)
                    if match and not line.lstrip().startswith("#"):
                        record(match.group(1), path)
            elif name == "pyproject.toml":
                data = tomllib.loads(content)
                project = data.get("project", {})
                deps = project.get("dependencies", [])
                if isinstance(deps, list):
                    supported = True
                    for dependency in deps:
                        match = re.match(r"\s*([A-Za-z0-9_.-]+)", str(dependency))
                        if match:
                            record(match.group(1), path)
                poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
                if isinstance(poetry, dict):
                    supported = True
                    for dep in poetry:
                        if dep.lower() != "python":
                            record(str(dep), path)
            elif name == "package.json":
                data = json.loads(content)
                for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                    values = data.get(section, {})
                    if isinstance(values, dict):
                        supported = True
                        for dep in values:
                            record(str(dep), path)
            elif name == "go.mod":
                supported = True
                for line in content.splitlines():
                    if line.strip() and not line.lstrip().startswith(("module ", "go ", "require (", ")", "//")) and len(line.split()) >= 2 and "/" in line.split()[0]:
                        record(line.split()[0], path)
            elif name == "cargo.toml":
                data = tomllib.loads(content)
                for section in ("dependencies", "dev-dependencies", "build-dependencies"):
                    values = data.get(section, {})
                    if isinstance(values, dict):
                        supported = True
                        for dep in values:
                            record(str(dep), path)
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, AttributeError, TypeError):
            continue
    return dependencies, supported


def _expected_manifests(command: str) -> tuple[set[str], str] | None:
    lowered = command.lower()
    lowered = re.sub(r"^(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+", "", lowered)
    command = re.sub(r"^(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+", "", command, flags=re.IGNORECASE)
    if lowered.startswith(("pip install", "pip3 install")):
        if "-r " in lowered or "--requirement " in lowered:
            match = re.search(r"(?:-r|--requirement)\s+([^\s]+)", command, re.IGNORECASE)
            path = match.group(1).strip("\"'").replace("\\", "/") if match else ""
            while path.startswith("./"):
                path = path[2:]
            return ({path} if path else set(), "requirements file")
        return set(), "Python project manifest"
    if lowered.startswith(("npm install", "npm i")):
        return {"package.json"}, "Node.js package manifest"
    if lowered.startswith(("yarn install", "yarn i", "pnpm install", "pnpm i")):
        return {"package.json"}, "Node.js package manifest"
    if lowered.startswith("poetry install"):
        return {"pyproject.toml"}, "pyproject.toml"
    if lowered.startswith("uv sync"):
        return {"pyproject.toml"}, "pyproject.toml"
    if lowered.startswith("go mod download"):
        return {"go.mod"}, "go.mod"
    if lowered.startswith("cargo build"):
        return {"cargo.toml"}, "Cargo.toml"
    if lowered.startswith("composer install"):
        return {"composer.json"}, "composer.json"
    return None


def _suggest_manifest_command(files: dict[str, str]) -> str:
    names = {PurePosixPath(path).name.lower() for path in files}
    if "requirements.txt" in names:
        return "pip install -r requirements.txt"
    if "package.json" in names:
        return "npm install"
    if "pyproject.toml" in names:
        return "poetry install"
    if "go.mod" in names:
        return "go mod download"
    if "cargo.toml" in names:
        return "cargo build"
    if "composer.json" in names:
        return "composer install"
    return ""


def _verify_path(claim: str, files: dict[str, str]) -> tuple[str, str, str]:
    path_claim = claim.strip("`'\".,;:()[]{}")
    normalized = path_claim.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.strip("/")
    matches = [path for path in files if path == normalized]
    if matches:
        return "Verified", f"Found repository file: {matches[0]}", ""
    if "/" not in normalized:
        basename_matches = [
            path for path in files
            if PurePosixPath(path).name.lower() == normalized.lower()
        ]
        if len(basename_matches) == 1:
            return "Verified", f"Found unique repository file matching `{normalized}`: {basename_matches[0]}", ""
        if len(basename_matches) > 1:
            return "Unverified", f"`{normalized}` matches multiple archive files: {', '.join(basename_matches[:5])}. Specify a repository-relative path.", ""
    actual_paths = list(files)
    suggestions = get_close_matches(normalized, actual_paths, n=1, cutoff=0.78)
    correction = f"Did you mean `{suggestions[0]}`?" if suggestions else ""
    return "Failed", f"No archive file matches `{normalized}` after path normalization ({len(files)} archive files checked).", correction


def _normalize_readme_path(path: str) -> str:
    normalized = path.strip().strip("`'\"").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    while normalized.startswith("/"):
        normalized = normalized[1:]
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_locally_generated_venv_path(claim: str, readme: str) -> bool:
    normalized_claim = _normalize_readme_path(claim)
    parts = PurePosixPath(normalized_claim).parts
    if len(parts) != 3 or parts[0].lower() not in {"venv", ".venv"} or parts[1].lower() != "scripts":
        return False
    if parts[2].lower() not in {"activate.ps1", "python.exe"}:
        return False

    for match in LOCAL_VENV_CREATE_PATTERN.finditer(readme):
        created_path = _normalize_readme_path(match.group(1))
        if created_path.lower() == parts[0].lower():
            return True
    return False


def _documented_paths(line: str) -> list[tuple[str, bool]]:
    """Extract repository paths and flag bare references with ambiguous context."""
    candidates: list[tuple[str, bool]] = []
    link_destination_ranges: list[tuple[int, int]] = []
    for match in MARKDOWN_LINK.finditer(line):
        target = match.group(1).split("#", 1)[0].split("?", 1)[0]
        target_start = match.start(1)
        link_destination_ranges.append((target_start, target_start + len(match.group(1))))
        if not target or re.match(r"(?i)^(?:https?:|mailto:|#)", target):
            continue
        label = match.group(0).split("](", 1)[0].lower()
        prefix = line[:match.start()].rstrip()
        explicit = bool(EXPLICIT_FILE_CONTEXT.search(prefix)) or bool(re.search(r"\b(?:file|path|script|module)\b", label))
        contextual = bool(AMBIGUOUS_REFERENCE_CONTEXT.search(prefix))
        has_path_structure = "/" in target or "\\" in target
        if has_path_structure or explicit:
            candidates.append((target, True))
        elif contextual:
            candidates.append((target, False))

    spans = [(match.start(1), match.group(1), True) for match in re.finditer(r"`([^`]+)`", line)]
    spans.append((0, line, False))
    for span_start, span, is_code in spans:
        command_matches = list(COMMAND_PATTERN.finditer(span))
        if command_matches:
            span = span[:command_matches[0].start()]
        for match in PATH_PATTERN.finditer(span):
            candidate = match.group(0)
            candidate_start = span_start + match.start()
            if any(start <= candidate_start < end for start, end in link_destination_ranges):
                continue
            prefix = (line[:span_start] + span[:match.start()]).rstrip("`'\" ")
            has_path_structure = "/" in candidate or "\\" in candidate
            explicit_file_context = bool(EXPLICIT_FILE_CONTEXT.search(prefix))
            ambiguous_context = bool(AMBIGUOUS_REFERENCE_CONTEXT.search(prefix))
            if has_path_structure or explicit_file_context:
                candidates.append((candidate, True))
            elif ambiguous_context and (is_code or prefix):
                candidates.append((candidate, False))

    results: list[tuple[str, bool]] = []
    for candidate, reliable_context in candidates:
        candidate = candidate.strip().strip("<>\"'.,;:()[]{}")
        if not candidate or candidate.lower() == "readme.md":
            continue
        item = (candidate, reliable_context)
        if item not in results:
            results.append(item)
    return results


def _runtime_evidence(runtime: str, version: str, files: dict[str, str]) -> tuple[str, str]:
    """Verify a runtime version only when a supported manifest states it exactly."""
    runtime = runtime.lower()
    for path, content in files.items():
        filename = PurePosixPath(path).name.lower()
        declared: list[str] = []
        if runtime == "python" and filename == "pyproject.toml":
            try:
                requires_python = tomllib.loads(content).get("project", {}).get("requires-python", "")
                declared.extend(re.findall(r"\d+(?:\.\d+){0,2}", str(requires_python)))
            except (tomllib.TOMLDecodeError, AttributeError, TypeError):
                continue
        elif runtime in {"node", "node.js"} and filename == "package.json":
            try:
                engines = json.loads(content).get("engines", {})
                node_engine = engines.get("node", "") if isinstance(engines, dict) else ""
                declared.extend(re.findall(r"\d+(?:\.\d+){0,2}", str(node_engine)))
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
        if version in declared:
            return "Verified", f"{path} declares the {runtime} version {version}."
        if declared:
            return "Unverified", f"{path} declares {runtime} version constraint(s) {', '.join(declared)}, which do not exactly establish README version {version}."
    return "Unverified", f"No supported manifest declares the {runtime} version {version}."


def _claim_rule(kind: str) -> str:
    rules = {
        "File path": "A documented repository-relative path must match a safe, successfully inspected archive file.",
        "Installation command": "The command's required manifest must exist in the archive at the path and working-directory level the command implies.",
        "Runtime requirement": "A supported manifest must declare the exact runtime version stated in the README.",
        "Dependency": "A supported, successfully parsed dependency manifest must declare the named package.",
        "Configuration variable": "The exact variable name must occur in an inspected non-README text file.",
    }
    return rules.get(kind, "Apply the analyzer's deterministic checks using the available archive evidence.")


def _claim_reason(status: str, evidence: str) -> str:
    if status == "Failed":
        return f"The README claim does not match the archive evidence: {evidence}"
    if status == "Unverified":
        return f"The available evidence is insufficient to decide reliably: {evidence}"
    return f"The rule found supporting evidence: {evidence}"


def _claim_correction(kind: str, status: str, claim: str, evidence: str = "") -> str:
    if status == "Failed":
        if kind == "File path":
            suggestion = re.search(r"Did you mean `([^`]+)`\?", evidence)
            if suggestion:
                return f"Update the README path to `{suggestion.group(1)}`; that file exists in the archive."
            return "Check whether the intended file is missing from the repository or the README path is incorrect; update the README or repository accordingly."
        if kind == "Installation command":
            supported_command = re.search(r"Repository evidence supports `([^`]+)`", evidence)
            if supported_command:
                return f"Use `{supported_command.group(1)}` in the README; it matches a manifest present in the archive."
            return "Review the archive's package manifests and update the README command to match one that is actually present, or include the intended manifest."
        if kind == "Dependency":
            manifests = re.search(r"dependency manifest\(s\): (.+)\.$", evidence)
            if manifests:
                return (
                    f"If `{claim}` is required, add it to the existing manifest(s) "
                    f"{manifests.group(1)}; otherwise remove or clarify the README reference."
                )
            return f"Check whether `{claim}` is required; if so, declare it in the existing dependency manifest, otherwise update the README reference."
    if status == "Unverified":
        if kind == "File path":
            if "virtual environment generated locally" in evidence:
                return "No README change is needed; the virtual environment is created locally and is not expected in the repository archive."
            if "possible reference" in evidence:
                return "Clarify in the README whether this is a repository-relative file path; if it is, provide its exact path."
            return "Confirm whether this is intended to be a repository file; if so, document its repository-relative path and include it in the archive."
        if kind == "Installation command":
            if "at " in evidence and "working directory" in evidence:
                return f"Specify the directory containing the manifest in the README command instructions. {evidence}"
            return "Review the command and its working-directory context against the package manifest; confirm manually if the analyzer's supported rules do not cover it."
        if kind == "Runtime requirement":
            declared = re.search(r"([^ ]+) declares .* constraint\(s\) ([^,]+(?:, [^,]+)*),", evidence)
            if declared:
                return (
                    f"Review the README runtime requirement against `{declared.group(1)}` "
                    f"which declares {declared.group(2)}; change the README or manifest only after confirming the supported range."
                )
            return "Review the runtime version declared by the project and clarify the README requirement if it differs or is not recorded in a supported manifest."
        if kind == "Dependency":
            return f"Inspect the supported dependency manifests to confirm whether `{claim}` is required and declared."
        if kind == "Configuration variable":
            return f"Inspect configuration and source files to confirm whether `{claim}` is required; document its source or intended configuration location."
    return ""


def analyze_repository(archive_bytes: bytes, repository_name: str = "Uploaded repository") -> RepositoryAnalysis:
    (
        files,
        repository_paths,
        file_metadata,
        integrity_issues,
        inspection_complete,
        available_paths,
    ) = _read_repository_archive(archive_bytes)
    readme_path = _find_readme(files)
    dependencies, has_dependency_manifest = _dependency_names(files)
    non_readme_contents = {
        path: text for path, text in files.items() if path != readme_path
    }
    metadata_paths = [
        item.safe_path
        for item in file_metadata
        if item.entry_type == "File" and item.safe_path
    ]
    candidate_documents: list[str] = []

    def include_document(path: str) -> None:
        if path not in candidate_documents:
            candidate_documents.append(path)

    if readme_path:
        include_document(readme_path)
    else:
        unreadable_readmes = [
            path for path in metadata_paths
            if PurePosixPath(path).name.casefold() == "readme.md"
        ]
        root_readmes = [path for path in unreadable_readmes if "/" not in path]
        if root_readmes:
            include_document(sorted(root_readmes)[0])
        elif unreadable_readmes:
            include_document(
                sorted(unreadable_readmes, key=lambda path: (path.count("/"), path.lower()))[0]
            )

    for wanted in ("CONTRIBUTING.md", "INSTALL.md", "SETUP.md"):
        for path in sorted(metadata_paths, key=str.casefold):
            if "/" not in path and path.casefold() == wanted.casefold():
                include_document(path)
    for path in sorted(metadata_paths, key=str.casefold):
        parts = PurePosixPath(path).parts
        if (
            len(parts) == 2
            and parts[0].casefold() == "docs"
            and PurePosixPath(path).suffix.casefold() in {".md", ".rst"}
        ):
            include_document(path)

    candidate_documents = candidate_documents[:10]
    readable_documents = [
        (path, files[path])
        for path in candidate_documents
        if path in files
    ]
    documents_checked: list[DocumentCheck] = []
    for path in candidate_documents:
        if path in files:
            documents_checked.append(DocumentCheck(path, "Checked", 0))
            continue
        metadata = next((item for item in file_metadata if item.safe_path == path), None)
        issue = next((item for item in integrity_issues if item.path == (metadata.path if metadata else path)), None)
        reason = (
            f"{issue.problem_type}: {issue.details}"
            if issue
            else metadata.inspection_details or (metadata.inspection_status if metadata else "No readable text was available.")
        )
        documents_checked.append(DocumentCheck(path, f"Not checked ({reason})", 0, reason))

    document_contents = tuple(readable_documents)
    readme = files.get(readme_path, "") if readme_path else ""
    claims: list[ClaimResult] = []
    seen: set[tuple[str, str, int, str]] = set()
    current_source_document = readme_path or ""

    def add(kind: str, text: str, line: int, status: str, evidence: str, correction: str = "") -> None:
        key = (kind, text.strip(), line, current_source_document)
        if key not in seen:
            seen.add(key)
            correction = correction or _claim_correction(kind, status, text.strip(), evidence)
            if status == "Failed" and kind == "File path":
                suggestion = re.search(r"Did you mean `([^`]+)`\?", correction)
                if suggestion:
                    correction = (
                        f"Update the README path to `{suggestion.group(1)}`; "
                        "that file exists in the archive."
                    )
            elif status == "Failed" and kind == "Installation command":
                supported_command = re.search(r"Repository evidence supports `([^`]+)`", correction)
                if supported_command:
                    correction = (
                        f"Use `{supported_command.group(1)}` in the README; "
                        "it matches a manifest present in the archive."
                    )
            claims.append(
                ClaimResult(
                    kind,
                    text.strip(),
                    line,
                    status,
                    evidence,
                    correction,
                    _claim_rule(kind),
                    _claim_reason(status, evidence),
                    current_source_document,
                )
            )

    for source_document, document_content, line_number, line in (
        (source, content, number, line)
        for source, content in readable_documents
        for number, line in enumerate(content.splitlines(), 1)
    ):
        current_source_document = source_document
        for match in RUNTIME_PATTERN.finditer(line):
            runtime = match.group(1)
            version = match.group(2)
            status, evidence = _runtime_evidence(runtime, version, files)
            add("Runtime requirement", match.group(0), line_number, status, evidence)

        command_texts = [
            match.group(1)
            for code in re.findall(r"`([^`]+)`", line)
            for match in COMMAND_PATTERN.finditer(code)
        ]
        command_texts.extend(match.group(1) for match in COMMAND_PATTERN.finditer(line))
        for command_text in dict.fromkeys(command_texts):
            command = command_text.strip().rstrip(".,;")
            expectation = _expected_manifests(command)
            if expectation is None:
                add("Installation command", command, line_number, "Unverified", "Command is not in the supported deterministic checks.")
                continue
            expected, label = expectation
            expected_lower = {item.lower() for item in expected}
            matched_manifests = [
                path for path in repository_paths
                if path.lower() in expected_lower
                or (all("/" not in item for item in expected) and PurePosixPath(path).name.lower() in expected_lower)
            ]
            readable_manifests = [
                path for path in available_paths
                if path.lower() in expected_lower
                or (all("/" not in item for item in expected) and PurePosixPath(path).name.lower() in expected_lower)
            ]
            if not expected:
                add("Installation command", command, line_number, "Unverified", "The command does not name a repository manifest, so its success cannot be established from repository files alone.")
            elif any(path.lower() in expected_lower for path in readable_manifests):
                root_manifest = next(path for path in readable_manifests if path.lower() in expected_lower)
                add("Installation command", command, line_number, "Verified", f"Found required {label}: {root_manifest}.")
            elif matched_manifests:
                unavailable = [path for path in matched_manifests if path not in available_paths]
                if unavailable:
                    details = [
                        f"{issue.path} ({issue.problem_type}: {issue.details})"
                        for issue in integrity_issues
                        if issue.path in unavailable
                    ]
                    detail_text = f" Details: {'; '.join(details)}." if details else ""
                    evidence = (
                        f"Found {label} path(s) {', '.join(unavailable)}, but those member(s) "
                        f"could not be inspected successfully.{detail_text}"
                    )
                else:
                    evidence = f"Found {label} at {', '.join(matched_manifests)}, but the README command does not establish that it runs from that directory."
                add("Installation command", command, line_number, "Unverified", evidence)
            elif not inspection_complete:
                add("Installation command", command, line_number, "Unverified", f"Could not establish whether the required {label} is absent because archive inspection was incomplete.")
            else:
                correction = _suggest_manifest_command(files)
                add("Installation command", command, line_number, "Failed", f"Required {label} `{', '.join(sorted(expected))}` is absent from {len(repository_paths)} normalized archive paths.", f"Repository evidence supports `{correction}`." if correction and correction != command else "")
            normalized_command = re.sub(r"^(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+", "", command, flags=re.IGNORECASE)
            for dependency in re.findall(r"(?:pip(?:3)?\s+install|npm\s+(?:install|i)|yarn\s+add|pnpm\s+add)\s+([A-Za-z0-9_.@/-]+)", normalized_command, re.IGNORECASE):
                package = dependency.split("==")[0].lower().replace("_", "-")
                if package.startswith("-"):
                    continue
                if not has_dependency_manifest:
                    unavailable_manifests = [
                        issue for issue in integrity_issues
                        if PurePosixPath(issue.path).name.casefold() in MANIFEST_NAMES
                        and issue.problem_type in {
                            "CRC failure",
                            "Unreadable member",
                            "Undecodable text",
                            "Unsupported compression",
                            "Encrypted entry",
                        }
                    ]
                    if unavailable_manifests:
                        evidence = "; ".join(
                            f"{issue.path} ({issue.problem_type}: {issue.details})"
                            for issue in unavailable_manifests
                        )
                        add(
                            "Dependency",
                            package,
                            line_number,
                            "Unverified",
                            f"Dependency declarations could not be checked because manifest contents are unavailable: {evidence}.",
                        )
                    else:
                        add("Dependency", package, line_number, "Unverified", "No successfully parsed supported dependency manifest is available to check package declarations.")
                elif package in dependencies:
                    add("Dependency", package, line_number, "Verified", f"Declared in {dependencies[package]}.")
                elif not inspection_complete:
                    add("Dependency", package, line_number, "Unverified", f"`{package}` was not found in the readable manifest(s), but archive inspection was incomplete, so absence cannot be established.")
                else:
                    manifest_paths = [path for path in files if PurePosixPath(path).name.lower() in MANIFEST_NAMES]
                    add("Dependency", package, line_number, "Failed", f"`{package}` is not declared in inspected dependency manifest(s): {', '.join(manifest_paths)}.")

        for candidate, reliable_context in _documented_paths(line):
            if not reliable_context:
                add("File path", candidate, line_number, "Unverified", f"`{candidate}` is mentioned as a possible reference, but the README does not establish that it is a repository-relative file path.")
                continue
            if re.search(r"[*?{}]", candidate):
                add("File path", candidate, line_number, "Unverified", "The documented path contains a wildcard or placeholder and cannot be matched to one archive file.")
                continue
            status, evidence, correction = _verify_path(candidate, {path: "" for path in available_paths})
            normalized_candidate = _normalize_readme_path(candidate).casefold()
            candidate_basename = PurePosixPath(normalized_candidate).name
            unavailable_matches = [
                item for item in file_metadata
                if item.entry_type == "File"
                and item.safe_path
                and (
                    _normalize_readme_path(item.safe_path).casefold() == normalized_candidate
                    or (
                        "/" not in normalized_candidate
                        and PurePosixPath(item.safe_path).name.casefold() == candidate_basename
                    )
                )
                and item.inspection_status in {
                    "CRC failure",
                    "Unreadable member",
                    "Undecodable text",
                    "Unsupported compression",
                    "Encrypted entry",
                }
            ]
            if unavailable_matches:
                status = "Unverified"
                issue_details = "; ".join(
                    f"{item.safe_path} ({item.inspection_status}: {item.inspection_details})"
                    for item in unavailable_matches
                )
                evidence = (
                    f"The README path may refer to an archive member that could not be inspected: "
                    f"{issue_details}. Its contents cannot support a Verified result."
                )
            is_local_venv = (
                status != "Verified"
                and _is_locally_generated_venv_path(candidate, document_content)
                and not any(
                    _normalize_readme_path(path).casefold()
                    == _normalize_readme_path(candidate).casefold()
                    for path in repository_paths
                )
            )
            if is_local_venv:
                status = "Unverified"
                evidence = (
                    f"`{candidate}` is inside a virtual environment generated locally by the "
                    "README's venv setup command. It is not expected to be included in the repository archive."
                )
                correction = "No README change is needed; the virtual environment is created locally."
            elif status == "Failed" and not inspection_complete:
                status = "Unverified"
                evidence = (
                    f"`{candidate}` was not found among the successfully inspected paths, but archive inspection "
                    "was incomplete. Missing-file status cannot be established reliably."
                )
            if source_document.casefold().startswith("docs/"):
                normalized_candidate = _normalize_readme_path(candidate).strip("/")
                docs_relative = str(PurePosixPath(source_document).parent / normalized_candidate)
                root_exists = normalized_candidate in available_paths
                docs_relative_exists = (
                    docs_relative != normalized_candidate
                    and docs_relative in available_paths
                )
                if docs_relative_exists and root_exists:
                    status = "Unverified"
                    evidence = (
                        f"Both repository-root path `{normalized_candidate}` and document-relative path "
                        f"`{docs_relative}` exist; the documentation reference is ambiguous."
                    )
                elif docs_relative_exists:
                    status = "Unverified"
                    evidence = (
                        f"`{docs_relative}` exists relative to this document, but the documented path is "
                        f"checked from the repository root as `{normalized_candidate}`. The intended target is ambiguous."
                    )
            add("File path", candidate, line_number, status, evidence, correction)

        for match in CONFIG_PATTERN.finditer(line):
            variable = match.group(1)
            evidence_path = next(
                (path for path, text in non_readme_contents.items() if re.search(rf"(?<![A-Za-z0-9]){re.escape(variable)}(?![A-Za-z0-9])", text)),
                None,
            )
            if evidence_path:
                add("Configuration variable", variable, line_number, "Verified", f"Found `{variable}` in {evidence_path}.")
            else:
                inspected = ", ".join(list(non_readme_contents)[:3]) or "no non-README text files"
                remainder = " and others" if len(non_readme_contents) > 3 else ""
                add("Configuration variable", variable, line_number, "Unverified", f"No occurrence of `{variable}` was found in {len(non_readme_contents)} inspected text files (including {inspected}{remainder}); absence alone does not establish that it is unsupported.")

    document_order = {
        path: index for index, (path, _) in enumerate(readable_documents)
    }
    claims.sort(key=lambda claim: (
        document_order.get(claim.source_document, len(document_order)),
        claim.line,
        claim.kind,
        claim.claim.lower(),
    ))
    claim_counts: dict[str, int] = {}
    for claim in claims:
        claim_counts[claim.source_document] = claim_counts.get(claim.source_document, 0) + 1
    documents_checked = [
        DocumentCheck(
            item.path,
            item.status,
            claim_counts.get(item.path, 0),
            item.reason,
        )
        for item in documents_checked
    ]
    return RepositoryAnalysis(
        repository_name,
        repository_paths,
        readme_path,
        tuple(claims),
        file_metadata,
        files.get(readme_path) if readme_path else None,
        integrity_issues,
        inspection_complete,
        tuple(
            item.safe_path for item in file_metadata
            if item.entry_type == "File" and item.safe_path and item.inspection_status not in {"Inspected (UTF-8)", "Inspected (binary)"}
        ),
        tuple(documents_checked),
        document_contents,
        tuple(files.items()),
    )


def summarize_claims(claims: Iterable[ClaimResult]) -> dict[str, int]:
    counts = {"total": 0, "verified": 0, "failed": 0, "unverified": 0}
    for claim in claims:
        counts["total"] += 1
        counts[claim.status.lower()] += 1
    return counts
