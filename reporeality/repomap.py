"""Build an evidence-backed, static repository relationship map."""

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath
from typing import Mapping

from .analyzer import RepositoryAnalysis

MAX_MAP_NODES = 200
MAX_PYTHON_FILE_SIZE = 200 * 1024
MANIFEST_NAMES = {
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "go.mod",
    "cargo.toml",
    "composer.json",
    "gemfile",
    "pom.xml",
}
ENTRYPOINT_NAMES = {"app.py", "main.py", "manage.py", "index.js"}
RUN_COMMAND = re.compile(
    r"(?i)\b(?:streamlit\s+run|python(?:3(?:\.\d+)?)?|py|node)\s+"
    r"([A-Za-z0-9_./\\-]+\.(?:py|js))"
)


def _normalize_path(path: str) -> str:
    normalized = path.strip().strip("`'\"").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _path_node_id(path: str) -> str:
    return f"path:{_normalize_path(path).casefold()}"


def _dot_escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _display_label(label: str) -> str:
    if len(label) <= 28:
        return label
    filename = PurePosixPath(label).name
    return filename if len(filename) <= 28 else f"{filename[:25]}..."


def build_repo_map_dot(
    nodes: tuple[dict[str, object], ...],
    edges: tuple[dict[str, object], ...],
) -> str:
    """Render the already-built map data as a styled left-to-right DOT graph."""
    lines = [
        "digraph RepoMap {",
        'graph [bgcolor="transparent", rankdir=LR, ranksep=1.0, nodesep=0.5, '
        'splines=true, size="14,8", ratio=compress, pad="0.2"];',
        'node [shape=box, style="rounded,filled", fontname="Segoe UI", fontsize=10, '
        'fontcolor="#ffffff", color="#303a46", fillcolor="#11161d", margin="0.14,0.09"];',
        'edge [fontname="Segoe UI", fontsize=8, fontcolor="#c9d1d9", penwidth=2, arrowsize=0.7];',
    ]
    node_styles = {
        "Documentation": ('fillcolor="#1f6feb", color="#1f6feb", style="rounded,filled"'),
        "Entry point": ('fillcolor="#238636", color="#3fb950", penwidth=2, style="rounded,filled"'),
        "Dependency manifest": ('fillcolor="#8957e5", color="#8957e5", style="rounded,filled"'),
        "Python module": ('fillcolor="#0d8a8a", color="#0d8a8a", style="rounded,filled"'),
        "Gap": ('fillcolor="#da3633", color="#da3633", style="rounded,filled,dashed"'),
        "Created locally": ('fillcolor="#6e7681", color="#6e7681", style="rounded,filled,dotted"'),
        "File": ('fillcolor="#30363d", color="#6e7681", style="rounded,filled"'),
    }
    for node in nodes:
        full_label = str(node["label"])
        tooltip = full_label
        if node.get("source"):
            tooltip += f" — {node['source']} line {node['line']}"
        if node.get("correction"):
            tooltip += f" — {node['correction']}"
        style = node_styles.get(str(node["kind"]), node_styles["File"])
        lines.append(
            f'"{_dot_escape(node["id"])}" [label="{_dot_escape(_display_label(full_label))}", '
            f'tooltip="{_dot_escape(tooltip)}", {style}];'
        )
    edge_labels = {
        "documented file": "links",
        "missing documented file": "missing",
        "documented local path": "local",
        "install command": "installs",
        "run command": "runs",
        "Python import": "imports",
    }
    for edge in edges:
        if edge["status"] == "gap":
            color, style = "#da3633", "dashed"
        elif edge["status"] == "local":
            color, style = "#6e7681", "dotted"
        elif edge["kind"] == "Python import":
            color, style = "#8b949e", "solid"
        else:
            color, style = "#3fb950", "solid"
        label = edge_labels.get(str(edge["kind"]), str(edge["kind"]))
        lines.append(
            f'"{_dot_escape(edge["source"])}" -> "{_dot_escape(edge["target"])}" '
            f'[color="{color}", style="{style}", label="{_dot_escape(label)}", '
            f'tooltip="{_dot_escape(edge["evidence"])}"];'
        )
    lines.append("}")
    return "\n".join(lines)


def _python_imports(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def build_repo_map(
    analysis: RepositoryAnalysis,
    decoded_files: Mapping[str, str],
) -> dict[str, tuple[dict[str, object], ...]]:
    """Return bounded nodes and evidence-bearing edges from existing analysis data."""
    archive_paths = set(analysis.files)
    nodes: dict[str, dict[str, object]] = {}
    edges: list[dict[str, object]] = []
    path_nodes: dict[str, str] = {}

    def add_node(
        node_id: str,
        label: str,
        kind: str,
        status: str = "normal",
        source: str = "",
        line: int = 0,
        correction: str = "",
    ) -> str | None:
        if node_id in nodes:
            return node_id
        if len(nodes) >= MAX_MAP_NODES:
            return None
        nodes[node_id] = {
            "id": node_id,
            "label": label,
            "kind": kind,
            "status": status,
            "source": source,
            "line": line,
            "correction": correction,
        }
        return node_id

    def add_edge(
        source: str,
        target: str,
        kind: str,
        evidence: str,
        status: str = "linked",
    ) -> None:
        if source in nodes and target in nodes:
            edges.append({
                "source": source,
                "target": target,
                "kind": kind,
                "evidence": evidence,
                "status": status,
            })

    def add_path_node(path: str) -> str | None:
        existing = path_nodes.get(path)
        if existing:
            return existing
        filename = PurePosixPath(path).name.casefold()
        suffix = PurePosixPath(path).suffix.casefold()
        if filename in MANIFEST_NAMES:
            node_id = _path_node_id(path)
            kind = "Dependency manifest"
        elif suffix == ".py":
            node_id = _path_node_id(path)
            kind = "Entry point" if filename in ENTRYPOINT_NAMES else "Python module"
        elif filename in ENTRYPOINT_NAMES:
            node_id = _path_node_id(path)
            kind = "Entry point"
        else:
            node_id = _path_node_id(path)
            kind = "File"
        created = add_node(node_id, path, kind)
        if created:
            path_nodes[path] = created
        return created

    document_paths = [item.path for item in analysis.documents_checked]
    for path in document_paths:
        node_id = _path_node_id(path)
        if add_node(node_id, path, "Documentation"):
            path_nodes[path] = node_id

    for claim in analysis.claims:
        if claim.kind != "File path":
            continue
        source_id = path_nodes.get(claim.source_document, _path_node_id(claim.source_document))
        normalized = _normalize_path(claim.claim)
        local_only = ".venv" in normalized.casefold() and "generated locally" in claim.evidence.casefold()
        if local_only:
            target_id = f"local:{normalized}"
            if add_node(target_id, f"{normalized}\n(created locally)", "Created locally", "local", claim.source_document, claim.line):
                add_edge(source_id, target_id, "documented local path", claim.evidence, "local")
            continue

        target_path = None
        if claim.status == "Verified":
            exact = [path for path in archive_paths if _normalize_path(path).casefold() == normalized.casefold()]
            if len(exact) == 1:
                target_path = exact[0]
            else:
                basename = PurePosixPath(normalized).name.casefold()
                matches = [path for path in archive_paths if PurePosixPath(path).name.casefold() == basename]
                if len(matches) == 1:
                    target_path = matches[0]
        if target_path:
            target_id = add_path_node(target_path)
            if target_id:
                add_edge(source_id, target_id, "documented file", claim.evidence)
        elif claim.status == "Failed":
            target_id = add_node(
                f"gap:{claim.source_document}:{claim.line}:{normalized}",
                f"{normalized}\n(missing)",
                "Gap",
                "gap",
                claim.source_document,
                claim.line,
                claim.correction or claim.guidance()["Suggested fix"],
            )
            if target_id:
                add_edge(source_id, target_id, "missing documented file", claim.evidence, "gap")

    for path in sorted(archive_paths, key=str.casefold):
        filename = PurePosixPath(path).name.casefold()
        if filename in MANIFEST_NAMES:
            node_id = add_node(_path_node_id(path), path, "Dependency manifest")
            if node_id:
                path_nodes[path] = node_id

    python_paths = {
        path for path in archive_paths
        if PurePosixPath(path).suffix.casefold() == ".py"
    }
    run_targets: set[str] = set()
    for document_path, content in decoded_files.items():
        if document_path not in document_paths:
            continue
        run_targets.update(
            _normalize_path(match.group(1))
            for match in RUN_COMMAND.finditer(content)
        )

    entry_paths = {
        path for path in archive_paths
        if PurePosixPath(path).name.casefold() in ENTRYPOINT_NAMES
        or _normalize_path(path) in run_targets
    }
    for path in sorted(python_paths, key=str.casefold):
        kind = "Entry point" if path in entry_paths else "Python module"
        node_id = add_node(_path_node_id(path), path, kind)
        if node_id:
            path_nodes[path] = node_id
    for path in sorted(entry_paths - python_paths, key=str.casefold):
        node_id = add_node(_path_node_id(path), path, "Entry point")
        if node_id:
            path_nodes[path] = node_id

    for document_path, content in decoded_files.items():
        if document_path not in document_paths:
            continue
        source_id = path_nodes.get(document_path, _path_node_id(document_path))
        for line_number, line in enumerate(content.splitlines(), 1):
            for match in RUN_COMMAND.finditer(line):
                target = _normalize_path(match.group(1))
                if target in archive_paths:
                    target_id = path_nodes.get(target)
                    if target_id:
                        add_edge(source_id, target_id, "run command", f"{document_path}:{line_number}: {line.strip()}")

        install_commands = [
            claim for claim in analysis.claims
            if claim.source_document == document_path
            and claim.kind == "Installation command"
        ]
        for claim in install_commands:
            command = claim.claim.casefold()
            manifest_candidates = []
            if "pip install" in command or "-r " in command:
                manifest_candidates = [
                    path for path in archive_paths
                    if PurePosixPath(path).name.casefold() in {"requirements.txt", "pyproject.toml"}
                ]
            elif any(token in command for token in ("npm install", "npm i ", "yarn install", "pnpm install")):
                manifest_candidates = [
                    path for path in archive_paths
                    if PurePosixPath(path).name.casefold() in {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
                ]
            elif "cargo build" in command:
                manifest_candidates = [path for path in archive_paths if PurePosixPath(path).name.casefold() == "cargo.toml"]
            elif "go mod download" in command:
                manifest_candidates = [path for path in archive_paths if PurePosixPath(path).name.casefold() == "go.mod"]
            for path in manifest_candidates:
                target_id = path_nodes.get(path)
                if target_id:
                    add_edge(source_id, target_id, "install command", claim.evidence)

    module_nodes = {path: path_nodes.get(path) for path in python_paths}
    for source_path in sorted(python_paths, key=str.casefold):
        source_id = module_nodes.get(source_path)
        source_text = decoded_files.get(source_path)
        if source_id is None or source_text is None or len(source_text.encode("utf-8")) > MAX_PYTHON_FILE_SIZE:
            continue
        imports = _python_imports(source_text)
        for imported in imports:
            module_path = imported.replace(".", "/") + ".py"
            candidates = [
                path for path in python_paths
                if path == module_path
                or path.endswith("/" + module_path)
                or path == imported.replace(".", "/") + "/__init__.py"
                or path.endswith("/" + imported.replace(".", "/") + "/__init__.py")
            ]
            if len(candidates) == 1 and candidates[0] != source_path:
                target_id = module_nodes.get(candidates[0])
                if target_id:
                    add_edge(source_id, target_id, "Python import", f"{source_path} imports {imported}")

    return {"nodes": tuple(nodes.values()), "edges": tuple(edges)}
