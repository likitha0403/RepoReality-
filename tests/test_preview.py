import unittest

from reporeality.analyzer import ClaimResult
from reporeality.preview import build_readme_preview


class ReadmePreviewTests(unittest.TestCase):
    def test_builds_preview_from_unique_failed_claim_replacement(self) -> None:
        readme = "Install with `pip install -r requirments.txt`.\nNext step.\n"
        claim = ClaimResult(
            kind="Installation command",
            claim="pip install -r requirments.txt",
            line=1,
            status="Failed",
            evidence="Required manifest `requirements.txt` is absent.",
            correction="Repository evidence supports `pip install -r requirements.txt`.",
        )

        self.assertEqual(
            build_readme_preview(readme, claim),
            "Install with `pip install -r requirements.txt`.\nNext step.\n",
        )

    def test_does_not_preview_non_failed_or_unsupported_claims(self) -> None:
        readme = "See `src/main.py`.\n"
        verified = ClaimResult("File path", "src/main.py", 1, "Verified", "Found file.")
        unsupported = ClaimResult("File path", "src/main.py", 1, "Failed", "Not found.")

        self.assertIsNone(build_readme_preview(readme, verified))
        self.assertIsNone(build_readme_preview(readme, unsupported))

    def test_does_not_preview_ambiguous_line_replacement(self) -> None:
        readme = "See `src/missing.py` and `src/missing.py`.\n"
        claim = ClaimResult(
            "File path",
            "src/missing.py",
            1,
            "Failed",
            "No matching archive file.",
            "Did you mean `src/main.py`?",
        )

        self.assertIsNone(build_readme_preview(readme, claim))


if __name__ == "__main__":
    unittest.main()
