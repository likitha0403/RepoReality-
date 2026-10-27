import io
import unittest
import zipfile

from reporeality.analyzer import analyze_repository
from reporeality.repomap import build_repo_map


def make_zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


class RepoMapTests(unittest.TestCase):
    def test_existing_documented_file_creates_linked_edge(self) -> None:
        analysis = analyze_repository(make_zip({
            "README.md": "The file `src/main.py` is the entry point.\n",
            "src/main.py": "print('ready')\n",
        }))
        repo_map = build_repo_map(analysis, dict(analysis.decoded_files))
        edge = next(edge for edge in repo_map["edges"] if edge["kind"] == "documented file")
        self.assertEqual(edge["source"], "path:readme.md")
        self.assertEqual(edge["target"], "path:src/main.py")
        self.assertEqual(edge["status"], "linked")
        self.assertTrue(edge["evidence"])

    def test_missing_documented_file_creates_gap_node_and_edge(self) -> None:
        analysis = analyze_repository(make_zip({
            "README.md": "The file `src/missing.py` is the entry point.\n",
        }))
        repo_map = build_repo_map(analysis, dict(analysis.decoded_files))
        edge = next(edge for edge in repo_map["edges"] if edge["status"] == "gap")
        gap = next(node for node in repo_map["nodes"] if node["id"] == edge["target"])
        self.assertEqual(gap["kind"], "Gap")
        self.assertEqual(gap["line"], 1)
        self.assertTrue(gap["correction"])
        self.assertTrue(edge["evidence"])

    def test_python_import_creates_module_edge(self) -> None:
        analysis = analyze_repository(make_zip({
            "README.md": "# Project\n",
            "app.py": "import helpers\n",
            "helpers.py": "def run():\n    return True\n",
        }))
        repo_map = build_repo_map(analysis, dict(analysis.decoded_files))
        edge = next(edge for edge in repo_map["edges"] if edge["kind"] == "Python import")
        self.assertEqual(edge["source"], "path:app.py")
        self.assertEqual(edge["target"], "path:helpers.py")
        self.assertIn("imports helpers", edge["evidence"])

    def test_document_that_is_also_a_link_target_has_one_normalized_node(self) -> None:
        analysis = analyze_repository(make_zip({
            "README.md": "The file `docs/usage.md` contains the usage guide.\n",
            "docs/usage.md": "Usage details.\n",
        }))
        repo_map = build_repo_map(analysis, dict(analysis.decoded_files))
        matching_nodes = [
            node for node in repo_map["nodes"]
            if str(node["id"]).casefold() == "path:docs/usage.md"
        ]
        self.assertEqual(len(matching_nodes), 1)
        self.assertEqual(matching_nodes[0]["kind"], "Documentation")

    def test_python_syntax_error_is_skipped_without_crashing(self) -> None:
        analysis = analyze_repository(make_zip({
            "README.md": "# Project\n",
            "broken.py": "import valid\nif:\n",
            "valid.py": "value = 1\n",
        }))
        repo_map = build_repo_map(analysis, dict(analysis.decoded_files))
        self.assertFalse(any(edge["source"] == "path:broken.py" for edge in repo_map["edges"]))

    def test_local_venv_is_not_reported_as_gap(self) -> None:
        analysis = analyze_repository(make_zip({
            "README.md": "py -m venv .venv\nThe file ` .\\.venv\\Scripts\\python.exe` is the runtime.\n",
        }))
        repo_map = build_repo_map(analysis, dict(analysis.decoded_files))
        local_nodes = [node for node in repo_map["nodes"] if node["kind"] == "Created locally"]
        self.assertTrue(local_nodes)
        self.assertFalse(any(node["kind"] == "Gap" for node in repo_map["nodes"]))


if __name__ == "__main__":
    unittest.main()
