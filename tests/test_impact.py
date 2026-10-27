import io
import struct
import unittest
import zipfile

from reporeality.analyzer import analyze_repository
from reporeality.impact import assess_integrity_impact


def make_zip(files: dict[str, str | bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def corrupt_member(archive_bytes: bytes, member_name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        info = archive.getinfo(member_name)
    corrupted = bytearray(archive_bytes)
    name_length, extra_length = struct.unpack_from("<HH", corrupted, info.header_offset + 26)
    payload_offset = info.header_offset + 30 + name_length + extra_length
    corrupted[payload_offset] ^= 0xFF
    return bytes(corrupted)


def replace_compression(archive_bytes: bytes, member_name: str, compression: int) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        info = archive.getinfo(member_name)
    updated = bytearray(archive_bytes)
    struct.pack_into("<H", updated, info.header_offset + 8, compression)
    central_offset = archive_bytes.find(b"PK\x01\x02")
    while central_offset >= 0:
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", updated, central_offset + 28)
        name = bytes(updated[central_offset + 46:central_offset + 46 + name_length]).decode("utf-8")
        if name == member_name:
            struct.pack_into("<H", updated, central_offset + 10, compression)
            break
        central_offset = archive_bytes.find(
            b"PK\x01\x02",
            central_offset + 46 + name_length + extra_length + comment_length,
        )
    return bytes(updated)


class IntegrityImpactTests(unittest.TestCase):
    def test_corrupt_file_links_to_readable_source_reference(self) -> None:
        archive = make_zip({
            "README.md": "The file `assets/config.json` contains application settings.\n",
            "src/app.py": 'CONFIG_PATH = "assets/config.json"\n',
            "assets/config.json": '{"mode": "safe"}',
        }, compression=zipfile.ZIP_STORED)
        analysis = analyze_repository(corrupt_member(archive, "assets/config.json"))
        impact = assess_integrity_impact(analysis)[0]
        self.assertEqual(impact["path"], "assets/config.json")
        self.assertEqual(impact["error_type"], "CRC failure")
        self.assertTrue(any(
            reference["path"] == "src/app.py" and reference["line"] == 1
            for reference in impact["references"]
        ))
        self.assertIn("explicitly reference", impact["potential_impact"])
        self.assertNotIn("will crash", impact["potential_impact"].casefold())

    def test_corrupt_file_without_references_has_undetermined_impact(self) -> None:
        archive = make_zip({
            "README.md": "# Repository notes\n",
            "assets/unused.bin": b"data",
        }, compression=zipfile.ZIP_STORED)
        analysis = analyze_repository(corrupt_member(archive, "assets/unused.bin"))
        impact = assess_integrity_impact(analysis)[0]
        self.assertEqual(impact["references"], [])
        self.assertIn("Impact is undetermined", impact["potential_impact"])
        self.assertIn("does not prove", impact["potential_impact"])

    def test_manifest_dependent_check_is_unverified_and_recovery_matches_crc(self) -> None:
        archive = make_zip({
            "README.md": "Run `pip install -r requirements.txt`.\n",
            "requirements.txt": "requests>=2\n",
        }, compression=zipfile.ZIP_STORED)
        analysis = analyze_repository(corrupt_member(archive, "requirements.txt"))
        command = next(claim for claim in analysis.claims if claim.kind == "Installation command")
        self.assertEqual(command.status, "Unverified")
        self.assertIn("requirements.txt", command.evidence)
        impact = assess_integrity_impact(analysis)[0]
        self.assertTrue(any(check["status"] == "Unverified" for check in impact["affected_checks"]))
        self.assertIn("CRC", impact["recovery"])
        self.assertIn("clean copy", impact["recovery"])

    def test_unrelated_readable_path_check_remains_verified(self) -> None:
        archive = make_zip({
            "README.md": (
                "The file `src/healthy.py` contains the entry point.\n"
                "Run `pip install -r requirements.txt`.\n"
            ),
            "src/healthy.py": "print('ready')\n",
            "requirements.txt": "requests>=2\n",
        }, compression=zipfile.ZIP_STORED)
        analysis = analyze_repository(corrupt_member(archive, "requirements.txt"))
        path_claim = next(claim for claim in analysis.claims if claim.kind == "File path")
        command = next(claim for claim in analysis.claims if claim.kind == "Installation command")
        self.assertEqual(path_claim.status, "Verified")
        self.assertEqual(command.status, "Unverified")

    def test_recovery_recommendations_match_decoding_and_compression_errors(self) -> None:
        decode_analysis = analyze_repository(make_zip({
            "README.md": "# Documentation\n",
            "requirements.txt": b"\xff\xfe",
        }))
        decode_impact = assess_integrity_impact(decode_analysis)[0]
        self.assertEqual(decode_impact["error_type"], "Undecodable text")
        self.assertIn("encoding", decode_impact["recovery"])

        unsupported_archive = make_zip({
            "README.md": "# Documentation\n",
            "package.json": '{"dependencies": {}}',
        })
        unsupported_archive = replace_compression(unsupported_archive, "package.json", 99)
        unsupported_analysis = analyze_repository(unsupported_archive)
        unsupported_impact = assess_integrity_impact(unsupported_analysis)[0]
        self.assertEqual(unsupported_impact["error_type"], "Unsupported compression")
        self.assertIn("supported compression method", unsupported_impact["recovery"])


if __name__ == "__main__":
    unittest.main()
