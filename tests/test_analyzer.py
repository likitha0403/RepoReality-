import io
import struct
import unittest
import zipfile
from unittest.mock import patch

from reporeality.analyzer import (
    RepoArchiveError,
    analyze_repository,
    read_repository_archive,
    summarize_claims,
)


def make_zip(files: dict[str, str | bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def corrupt_member_payload(archive_bytes: bytes, member_name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        info = archive.getinfo(member_name)
    corrupted = bytearray(archive_bytes)
    name_length, extra_length = struct.unpack_from("<HH", corrupted, info.header_offset + 26)
    payload_offset = info.header_offset + 30 + name_length + extra_length
    corrupted[payload_offset] ^= 0xFF
    return bytes(corrupted)


def replace_member_compression(archive_bytes: bytes, member_name: str, compression: int) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        info = archive.getinfo(member_name)
    corrupted = bytearray(archive_bytes)
    struct.pack_into("<H", corrupted, info.header_offset + 8, compression)
    central_offset = archive_bytes.find(b"PK\x01\x02")
    while central_offset >= 0:
        filename_length, extra_length, comment_length = struct.unpack_from(
            "<HHH", corrupted, central_offset + 28
        )
        filename = bytes(corrupted[central_offset + 46:central_offset + 46 + filename_length])
        if filename.decode("utf-8") == member_name:
            struct.pack_into("<H", corrupted, central_offset + 10, compression)
            break
        next_offset = central_offset + 46 + filename_length + extra_length + comment_length
        if next_offset <= central_offset:
            break
        central_offset = archive_bytes.find(b"PK\x01\x02", next_offset)
    return bytes(corrupted)


def analyze_with_read_tracking(archive_bytes: bytes):
    read_paths: list[str] = []
    original_read = zipfile.ZipFile.read

    def tracking_read(archive: zipfile.ZipFile, name, pwd=None):
        read_paths.append(name.filename if isinstance(name, zipfile.ZipInfo) else str(name))
        return original_read(archive, name, pwd)

    with patch.object(zipfile.ZipFile, "read", tracking_read):
        result = analyze_repository(archive_bytes)
    return result, read_paths


class ArchiveTests(unittest.TestCase):
    def test_invalid_zip_is_reported(self) -> None:
        with self.assertRaises(RepoArchiveError):
            read_repository_archive(b"not a zip")

    def test_path_traversal_is_rejected(self) -> None:
        result, read_paths = analyze_with_read_tracking(make_zip({
            "README.md": "The file `src/main.py` exists.\n",
            "../outside.txt": "unsafe",
            "src/main.py": "print('safe')\n",
        }))

        rejected = next(issue for issue in result.integrity_issues if issue.problem_type == "Unsafe path")
        self.assertEqual(rejected.path, "../outside.txt")
        self.assertNotIn("../outside.txt", result.files)
        self.assertTrue(any(item.safe_path is None for item in result.file_metadata if item.path == "../outside.txt"))
        self.assertNotIn("../outside.txt", read_paths)
        self.assertEqual(
            next(claim for claim in result.claims if claim.claim == "src/main.py").status,
            "Verified",
        )

    def test_case_insensitive_collisions_are_rejected_after_root_normalization(self) -> None:
        archive = make_zip({
            "demo/README.md": "Run `pip install -r requirements.txt`.",
            "demo/readme.md": "Different README.",
        })
        result, read_paths = analyze_with_read_tracking(archive)

        collision_issues = [
            issue for issue in result.integrity_issues
            if issue.problem_type == "Duplicate or case-colliding path"
        ]
        self.assertEqual({issue.path for issue in collision_issues}, {"README.md", "readme.md"})
        self.assertIsNone(result.readme_path)
        self.assertTrue(all(
            item.inspection_status == "Duplicate or case-colliding path"
            for item in result.file_metadata
        ))
        self.assertNotIn("demo/README.md", read_paths)
        self.assertNotIn("demo/readme.md", read_paths)

    def test_crc_failure_in_manifest_cannot_verify_install_command(self) -> None:
        archive = make_zip({
            "README.md": "Run `pip install -r requirements.txt`.",
            "requirements.txt": "requests>=2\n",
        }, compression=zipfile.ZIP_STORED)
        archive = corrupt_member_payload(archive, "requirements.txt")

        result = analyze_repository(archive)

        issue = next(issue for issue in result.integrity_issues if issue.path == "requirements.txt")
        self.assertEqual(issue.problem_type, "CRC failure")
        command = next(claim for claim in result.claims if claim.kind == "Installation command")
        self.assertEqual(command.status, "Unverified")

    def test_invalid_utf8_in_supported_manifest_is_reported(self) -> None:
        archive = make_zip({
            "README.md": "Run `pip install -r requirements.txt`.",
            "requirements.txt": b"\xff\xfe",
        })

        result = analyze_repository(archive)

        issue = next(issue for issue in result.integrity_issues if issue.path == "requirements.txt")
        self.assertEqual(issue.problem_type, "Undecodable text")
        command = next(claim for claim in result.claims if claim.kind == "Installation command")
        self.assertEqual(command.status, "Unverified")

    def test_unsupported_compression_is_reported_as_archive_error(self) -> None:
        archive = make_zip({
            "README.md": "Run `npm install`.\n",
            "package.json": '{"dependencies": {}}',
        })
        archive = replace_member_compression(archive, "package.json", 99)

        result = analyze_repository(archive)

        issue = next(issue for issue in result.integrity_issues if issue.path == "package.json")
        self.assertEqual(issue.problem_type, "Unsupported compression")
        command = next(claim for claim in result.claims if claim.kind == "Installation command")
        self.assertEqual(command.status, "Unverified")

    def test_compressed_upload_size_limit_is_enforced_before_opening_zip(self) -> None:
        archive = make_zip({"README.md": "A README."})

        with patch("reporeality.analyzer.MAX_COMPRESSED_ARCHIVE_SIZE", len(archive) - 1):
            with self.assertRaisesRegex(RepoArchiveError, "Compressed archive exceeds"):
                analyze_repository(archive)

    def test_entry_count_limit_stops_processing(self) -> None:
        archive = make_zip({"README.md": "A README.", "src/main.py": "print('safe')"})

        with patch("reporeality.analyzer.MAX_ARCHIVE_FILES", 1):
            with self.assertRaisesRegex(RepoArchiveError, "entries"):
                analyze_repository(archive)

    def test_total_uncompressed_size_limit_stops_processing(self) -> None:
        archive = make_zip(
            {"README.md": "A README.", "src/main.py": "print('safe')"},
            compression=zipfile.ZIP_STORED,
        )

        with patch("reporeality.analyzer.MAX_TOTAL_UNCOMPRESSED", 10):
            with self.assertRaisesRegex(RepoArchiveError, "uncompressed inspection limit"):
                analyze_repository(archive)

    def test_compression_ratio_limit_is_enforced(self) -> None:
        archive = make_zip({"README.md": "x" * 10_000})
        with patch("reporeality.analyzer.MAX_COMPRESSION_RATIO", 2):
            with self.assertRaisesRegex(RepoArchiveError, "compression ratio"):
                analyze_repository(archive)

    def test_symlink_entries_are_rejected(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("README.md", "The file `src/main.py` exists.\n")
            archive.writestr("src/main.py", "print('safe')\n")
            link = zipfile.ZipInfo("src/link.py")
            link.create_system = 3
            link.external_attr = (0o120777 << 16)
            archive.writestr(link, "target.py")

        result, read_paths = analyze_with_read_tracking(buffer.getvalue())

        issue = next(issue for issue in result.integrity_issues if issue.path == "src/link.py")
        self.assertEqual(issue.problem_type, "Symbolic link")
        link_metadata = next(item for item in result.file_metadata if item.path == "src/link.py")
        self.assertEqual(link_metadata.inspection_status, "Symbolic link")
        self.assertNotIn("src/link.py", read_paths)
        self.assertEqual(
            next(claim for claim in result.claims if claim.claim == "src/main.py").status,
            "Verified",
        )

    def test_missing_readme_returns_empty_claims(self) -> None:
        result = analyze_repository(make_zip({"src/main.py": "print('safe')"}))
        self.assertIsNone(result.readme_path)
        self.assertEqual(result.claims, ())
        self.assertEqual(result.file_metadata[0].path, "main.py")
        self.assertEqual(result.file_metadata[0].size_bytes, len(b"print('safe')"))
        self.assertEqual(result.file_metadata[0].file_type, "Python")

    def test_single_top_level_project_folder_is_normalized(self) -> None:
        result = analyze_repository(make_zip({
            "demo-project/README.md": "See `src/main.py` and [logo](assets/logo.png).\n",
            "demo-project/src/main.py": "print('safe')\n",
            "demo-project/assets/logo.png": "binary placeholder",
            ".DS_Store": "archive metadata",
        }))
        self.assertEqual(result.readme_path, "README.md")
        self.assertIn("src/main.py", result.files)
        self.assertIn("assets/logo.png", result.files)
        metadata_by_path = {item.path: item for item in result.file_metadata}
        self.assertEqual(metadata_by_path["README.md"].size_bytes, len(
            "See `src/main.py` and [logo](assets/logo.png).\n".encode("utf-8")
        ))
        self.assertEqual(metadata_by_path["README.md"].file_type, "Markdown")
        self.assertEqual(metadata_by_path["src/main.py"].file_type, "Python")
        self.assertEqual(metadata_by_path["assets/logo.png"].file_type, "Image")
        path_claim = next(
            claim for claim in result.claims
            if claim.kind == "File path" and claim.claim == "src/main.py"
        )
        self.assertEqual(path_claim.status, "Verified")
        self.assertEqual(path_claim.evidence, "Found repository file: src/main.py")
        self.assertIn(
            ("assets/logo.png", "Verified"),
            {(claim.claim, claim.status) for claim in result.claims if claim.kind == "File path"},
        )

    def test_file_metadata_records_path_size_and_deterministic_type(self) -> None:
        contents = {
            "README.md": "# Project\n",
            "app.py": "print('hello')\n",
            "settings.toml": "[app]\nname = 'demo'\n",
            "notes.txt": "plain text\n",
            "assets/logo.png": b"\x89PNG\r\n\x1a\n",
            "LICENSE.unknown": b"\x00\x01\x02",
        }
        result = analyze_repository(make_zip(contents))
        metadata_by_path = {item.path: item for item in result.file_metadata}

        self.assertEqual(set(metadata_by_path), set(contents))
        self.assertEqual(metadata_by_path["README.md"].size_bytes, len(contents["README.md"].encode("utf-8")))
        self.assertEqual(metadata_by_path["app.py"].size_bytes, len(contents["app.py"].encode("utf-8")))
        self.assertEqual(metadata_by_path["README.md"].file_type, "Markdown")
        self.assertEqual(metadata_by_path["app.py"].file_type, "Python")
        self.assertEqual(metadata_by_path["settings.toml"].file_type, "Config")
        self.assertEqual(metadata_by_path["notes.txt"].file_type, "Text")
        self.assertEqual(metadata_by_path["assets/logo.png"].file_type, "Image")
        self.assertEqual(metadata_by_path["LICENSE.unknown"].file_type, "Other")
        self.assertEqual(result.readme_content, contents["README.md"])


class VerificationTests(unittest.TestCase):
    def test_docs_install_claim_records_source_and_line(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "# Project\n",
            "docs/INSTALL.md": (
                "# Installation\n"
                "The file `src/main.py` contains the entry point.\n"
            ),
            "src/main.py": "print('safe')\n",
        }))
        claim = next(
            claim for claim in result.claims
            if claim.claim == "src/main.py"
        )
        self.assertEqual(claim.source_document, "docs/INSTALL.md")
        self.assertEqual(claim.line, 2)
        self.assertEqual(claim.status, "Verified")
        self.assertEqual(claim.to_dict()["source"], "docs/INSTALL.md")
        self.assertEqual(
            next(item.claim_count for item in result.documents_checked if item.path == "docs/INSTALL.md"),
            1,
        )

    def test_unreadable_document_is_listed_not_checked_and_adds_no_claims(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "# Project\n",
            "docs/ENCODING.rst": b"Bad UTF-8: \xff\xfe",
        }))
        document = next(item for item in result.documents_checked if item.path == "docs/ENCODING.rst")
        self.assertTrue(document.status.startswith("Not checked (Undecodable text:"))
        self.assertEqual(document.claim_count, 0)
        self.assertFalse(any(claim.source_document == "docs/ENCODING.rst" for claim in result.claims))

    def test_docs_relative_path_collision_is_unverified(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "# Project\n",
            "docs/INSTALL.md": "The file `src/main.py` is the entry point.\n",
            "src/main.py": "print('root')\n",
            "docs/src/main.py": "print('docs')\n",
        }))
        claim = next(claim for claim in result.claims if claim.source_document == "docs/INSTALL.md")
        self.assertEqual(claim.status, "Unverified")
        self.assertIn("ambiguous", claim.evidence)

    def test_docs_relative_only_path_is_unverified_instead_of_guessed(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "# Project\n",
            "docs/INSTALL.md": "The file `src/main.py` is the entry point.\n",
            "docs/src/main.py": "print('docs')\n",
        }))
        claim = next(claim for claim in result.claims if claim.source_document == "docs/INSTALL.md")
        self.assertEqual(claim.status, "Unverified")
        self.assertIn("checked from the repository root", claim.evidence)
        self.assertEqual(claim.to_dict()["source"], "docs/INSTALL.md")

    def test_docs_are_checked_when_readme_is_absent(self) -> None:
        result = analyze_repository(make_zip({
            "INSTALL.md": "The file `src/main.py` contains the entry point.\n",
            "src/main.py": "print('safe')\n",
        }))
        self.assertIsNone(result.readme_path)
        claim = next(claim for claim in result.claims if claim.source_document == "INSTALL.md")
        self.assertEqual(claim.status, "Verified")
        self.assertEqual(claim.line, 1)

    def test_readme_only_claim_results_are_preserved(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "The file `src/main.py` exists.\nThe file `src/missing.py` is generated.\n",
            "src/main.py": "print('safe')\n",
        }))
        self.assertEqual(result.readme_path, "README.md")
        self.assertEqual(
            [(claim.claim, claim.line, claim.status) for claim in result.claims],
            [("src/main.py", 1, "Verified"), ("src/missing.py", 2, "Failed")],
        )
        self.assertEqual(
            [(document.path, document.status, document.claim_count) for document in result.documents_checked],
            [("README.md", "Checked", 2)],
        )

    def test_verifies_and_fails_readme_claims_with_evidence(self) -> None:
        repository = make_zip({
            "README.md": (
                "Run `pip install -r requirements.txt`.\n"
                "See `src/main.py` and `src/missing.py`.\n"
                "Set `APP_SECRET` before launch.\n"
            ),
            "requirements.txt": "requests>=2\n",
            "src/main.py": "APP_SECRET = ''\n",
        })
        result = analyze_repository(repository, "demo")
        by_claim = {(claim.kind, claim.claim): claim for claim in result.claims}
        self.assertEqual(by_claim[("Installation command", "pip install -r requirements.txt")].status, "Verified")
        self.assertEqual(by_claim[("File path", "src/main.py")].status, "Verified")
        self.assertEqual(by_claim[("File path", "src/missing.py")].status, "Failed")
        self.assertEqual(by_claim[("Configuration variable", "APP_SECRET")].status, "Verified")
        self.assertEqual(summarize_claims(result.claims)["total"], 4)

    def test_ordinary_os_executable_and_technology_mentions_are_not_paths(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": (
                "Use `python.exe` on Windows with Node.js and Git; Python is supported. "
                "\n[Python executable](python.exe) and [Node.js](Node.js) are technology references.\n"
            ),
        }))
        self.assertFalse([claim for claim in result.claims if claim.kind == "File path"])
        self.assertFalse([claim for claim in result.claims if claim.kind == "Runtime requirement"])
        self.assertFalse([claim for claim in result.claims if claim.kind == "Technology mention"])

    def test_contextual_markdown_links_to_bare_technology_names_are_unverified(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "See [python.exe](python.exe); see [Node.js](Node.js).\n",
        }))
        path_claims = [claim for claim in result.claims if claim.kind == "File path"]
        self.assertEqual({claim.claim for claim in path_claims}, {"python.exe", "Node.js"})
        self.assertTrue(all(claim.status == "Unverified" for claim in path_claims))
        self.assertTrue(all("does not establish" in claim.evidence for claim in path_claims))

    def test_executable_is_a_path_only_with_explicit_file_context(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "The file `python.exe` is included in this repository.\n",
            "bin/python.exe": "binary placeholder",
        }))
        path_claim = next(claim for claim in result.claims if claim.kind == "File path")
        self.assertEqual(path_claim.status, "Verified")
        self.assertEqual(path_claim.evidence, "Found unique repository file matching `python.exe`: bin/python.exe")

    def test_runtime_requirement_is_separate_from_technology_mentions(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "Requires Python 3.11 or later. Node.js is used for frontend tooling.\n",
            "pyproject.toml": '[project]\nrequires-python = ">=3.11"\n',
        }))
        runtime_claims = [claim for claim in result.claims if claim.kind == "Runtime requirement"]
        self.assertEqual(len(runtime_claims), 1)
        self.assertEqual(runtime_claims[0].status, "Verified")
        self.assertIn("pyproject.toml", runtime_claims[0].evidence)
        self.assertFalse([claim for claim in result.claims if claim.kind == "File path"])

    def test_runtime_requirement_without_manifest_evidence_is_unverified(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "Requires Python 3.12 or later.\n",
        }))
        claim = next(claim for claim in result.claims if claim.kind == "Runtime requirement")
        self.assertEqual(claim.status, "Unverified")
        self.assertIn("No supported manifest", claim.evidence)

    def test_missing_configuration_reference_is_unverified(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "Set `APP_SECRET` before starting.\n",
            "src/main.py": "print('ready')\n",
        }))
        claim = next(claim for claim in result.claims if claim.kind == "Configuration variable")
        self.assertEqual(claim.status, "Unverified")
        self.assertIn("src/main.py", claim.evidence)

    def test_windows_pip_command_and_manifest_path_are_verified(self) -> None:
        result = analyze_repository(make_zip({
            "demo/README.md": "In PowerShell, run `py -m pip install -r .\\requirements.txt`.\n",
            "demo/requirements.txt": "requests>=2\n",
        }))
        command = next(claim for claim in result.claims if claim.kind == "Installation command")
        self.assertEqual(command.status, "Verified")
        self.assertIn("requirements.txt", command.evidence)

    def test_nested_manifest_without_working_directory_context_is_unverified(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "Run `npm install`.\n",
            "frontend/package.json": '{"dependencies": {}}',
        }))
        command = next(claim for claim in result.claims if claim.kind == "Installation command")
        self.assertEqual(command.status, "Unverified")
        self.assertIn("frontend/package.json", command.evidence)

    def test_missing_file_reference_fails_with_archive_evidence(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "The file `src/not-there.py` contains the entry point.\n",
            "src/main.py": "print('safe')\n",
        }))
        claim = next(claim for claim in result.claims if claim.kind == "File path")
        self.assertEqual(claim.status, "Failed")
        self.assertIn("2 archive files checked", claim.evidence)

    def test_missing_repository_relative_path_remains_failed(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "See `src/missing.py` for the entry point.\n",
            "src/main.py": "print('safe')\n",
        }))
        claim = next(claim for claim in result.claims if claim.kind == "File path")
        self.assertEqual(claim.claim, "src/missing.py")
        self.assertEqual(claim.status, "Failed")
        self.assertIn("No archive file matches", claim.evidence)

    def test_missing_venv_activation_script_is_unverified_after_local_creation(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": (
                "Create the environment with `py -m venv venv`.\n"
                "Activate it with ` .\\venv\\Scripts\\Activate.ps1`.\n"
            ),
        }))
        claim = next(claim for claim in result.claims if claim.claim.endswith("Activate.ps1"))
        self.assertEqual(claim.status, "Unverified")
        self.assertIn("generated locally", claim.evidence)
        self.assertIn("not expected to be included", claim.evidence)

    def test_missing_venv_python_is_unverified_after_local_creation(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": (
                "Create the environment with `py -m venv venv`.\n"
                "Install packages with ` .\\venv\\Scripts\\python.exe -m pip install -r requirements.txt`.\n"
            ),
            "requirements.txt": "streamlit\n",
        }))
        claim = next(claim for claim in result.claims if claim.claim.endswith("python.exe"))
        self.assertEqual(claim.status, "Unverified")
        self.assertIn("generated locally", claim.evidence)
        self.assertIn("not expected to be included", claim.evidence)

    def test_readme_dot_venv_setup_paths_are_unverified_when_absent(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": (
                "From the repository folder, create and activate a virtual environment, install dependencies, and launch Streamlit:\n\n"
                "```powershell\n"
                "py -m venv .venv\n"
                ".\\.venv\\Scripts\\Activate.ps1\n"
                "python -m pip install -r requirements.txt\n"
                "python -m streamlit run app.py\n"
                "```\n\n"
                "If PowerShell blocks virtual-environment activation, use the environment's Python executable directly:\n\n"
                "```powershell\n"
                ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt\n"
                ".\\.venv\\Scripts\\python.exe -m streamlit run app.py\n"
                "```"
            ),
            "requirements.txt": "streamlit\n",
        }))
        venv_claims = [
            claim for claim in result.claims
            if claim.kind == "File path"
            and claim.claim.endswith(("Activate.ps1", "python.exe"))
        ]
        self.assertEqual(len(venv_claims), 3)
        self.assertEqual(
            sum(claim.claim.endswith("Activate.ps1") for claim in venv_claims),
            1,
        )
        self.assertEqual(sum(claim.claim.endswith("python.exe") for claim in venv_claims), 2)
        for claim in venv_claims:
            self.assertEqual(claim.status, "Unverified")
            self.assertIn("generated locally", claim.evidence)
            self.assertIn("not expected to be included", claim.evidence)

    def test_dashboard_venv_paths_at_lines_22_30_and_31_are_unverified(self) -> None:
        readme_lines = [""] * 31
        readme_lines[19] = "py -m venv .venv"
        readme_lines[21] = r".\.venv\Scripts\Activate.ps1"
        readme_lines[29] = r".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        readme_lines[30] = r".\.venv\Scripts\python.exe -m streamlit run app.py"
        result = analyze_repository(make_zip({
            "README.md": "\n".join(readme_lines),
            "requirements.txt": "streamlit\n",
        }))
        venv_claims = [
            claim for claim in result.claims
            if claim.kind == "File path"
            and claim.claim.endswith(("Activate.ps1", "python.exe"))
        ]
        self.assertEqual(
            [(claim.claim.rsplit("\\", 1)[-1], claim.line, claim.status) for claim in venv_claims],
            [
                ("Activate.ps1", 22, "Unverified"),
                ("python.exe", 30, "Unverified"),
                ("python.exe", 31, "Unverified"),
            ],
        )
        for claim in venv_claims:
            self.assertIn("generated locally", claim.evidence)
            self.assertIn("No README change is needed", claim.guidance()["Suggested fix"])

    def test_failed_claim_guidance_and_export_include_actionable_fields(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "The entry point is `src/mian.py`.\n",
            "src/main.py": "print('safe')\n",
        }))
        claim = next(claim for claim in result.claims if claim.kind == "File path")
        self.assertEqual(claim.status, "Failed")
        guidance = claim.guidance()
        self.assertEqual(
            set(guidance),
            {
                "README says",
                "Rule checked",
                "What we found",
                "Why it is flagged",
                "Type",
                "Suggested fix",
            },
        )
        self.assertEqual(guidance["README says"], "src/mian.py (README line 1)")
        self.assertIn("repository-relative path", guidance["Rule checked"])
        self.assertIn("No archive file matches", guidance["What we found"])
        self.assertIn("does not match", guidance["Why it is flagged"])
        self.assertEqual(guidance["Type"], "Documentation error (Failed)")
        self.assertIn("src/main.py", guidance["Suggested fix"])
        self.assertEqual(claim.to_dict()["guidance"], guidance)

    def test_unverified_claim_guidance_explains_human_review_and_fix(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "See `setup.py` for an example.\n",
            "src/main.py": "print('safe')\n",
        }))
        claim = next(claim for claim in result.claims if claim.kind == "File path")
        guidance = claim.guidance()
        self.assertEqual(claim.status, "Unverified")
        self.assertEqual(guidance["README says"], "setup.py (README line 1)")
        self.assertIn("possible reference", guidance["What we found"])
        self.assertIn("insufficient", guidance["Why it is flagged"])
        self.assertEqual(guidance["Type"], "Needs human review (Unverified)")
        self.assertIn("Clarify in the README", guidance["Suggested fix"])
        self.assertEqual(claim.to_dict()["guidance"], guidance)

    def test_existing_venv_file_remains_verified(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": (
                "Create the environment with `py -m venv venv`.\n"
                "Activate it with ` .\\venv\\Scripts\\Activate.ps1`.\n"
            ),
            "venv/Scripts/Activate.ps1": "Write-Output ready",
        }))
        claim = next(claim for claim in result.claims if claim.claim.endswith("Activate.ps1"))
        self.assertEqual(claim.status, "Verified")
        self.assertIn("Found repository file: venv/Scripts/Activate.ps1", claim.evidence)

    def test_existing_dot_venv_file_remains_verified(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": (
                "py -m venv .venv\n"
                ".\\.venv\\Scripts\\Activate.ps1\n"
            ),
            ".venv/Scripts/Activate.ps1": "Write-Output ready",
        }))
        claim = next(claim for claim in result.claims if claim.claim.endswith("Activate.ps1"))
        self.assertEqual(claim.status, "Verified")
        self.assertIn("Found repository file: .venv/Scripts/Activate.ps1", claim.evidence)

    def test_venv_path_without_local_creation_context_remains_failed(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "See `venv/Scripts/python.exe` for the application entry point.\n",
        }))
        claim = next(claim for claim in result.claims if claim.kind == "File path")
        self.assertEqual(claim.status, "Failed")
        self.assertIn("No archive file matches", claim.evidence)

    def test_bare_filename_in_weak_reference_context_is_unverified(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "See `setup.py` for an example.\n",
            "src/main.py": "print('safe')\n",
        }))
        claim = next(claim for claim in result.claims if claim.kind == "File path")
        self.assertEqual(claim.claim, "setup.py")
        self.assertEqual(claim.status, "Unverified")
        self.assertIn("does not establish", claim.evidence)

    def test_unrelated_command_is_not_reported_as_an_install_claim(self) -> None:
        result = analyze_repository(make_zip({"README.md": "Run `make magic`.\n"}))
        self.assertEqual(len(result.claims), 0)

    def test_dependency_claim_checks_manifest(self) -> None:
        result = analyze_repository(make_zip({
            "README.md": "Install with `pip install requests`.\n",
            "requirements.txt": "requests>=2\n",
        }))
        dependency = next(claim for claim in result.claims if claim.kind == "Dependency")
        self.assertEqual(dependency.status, "Verified")
        self.assertIn("requirements.txt", dependency.evidence)


if __name__ == "__main__":
    unittest.main()
