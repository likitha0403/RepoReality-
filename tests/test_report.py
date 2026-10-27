import io
import unittest
import zipfile

from reporeality.analyzer import ClaimResult, RepositoryAnalysis, analyze_repository, summarize_claims
from reporeality.report import build_html_report


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


class HtmlReportTests(unittest.TestCase):
    def test_html_report_contains_summary_counts_and_failed_suggestion(self) -> None:
        analysis = analyze_repository(make_zip({
            "README.md": "The file `src/mian.py` is the entry point.\n",
            "src/main.py": "print('ready')\n",
        }), "demo")
        html = build_html_report(
            analysis,
            summarize_claims(analysis.claims),
            "2026-10-27T00:00:00+00:00",
        )
        self.assertIn("<span>Total checks</span><strong>1</strong>", html)
        self.assertIn("<span>Failed</span><strong>1</strong>", html)
        self.assertIn("src/main.py", html)
        self.assertIn("Suggested fix", html)
        self.assertIn("Archive Integrity", html)
        self.assertIn("Incomplete evidence is not proof that the repository is valid.", html)
        self.assertNotIn("<script", html)

    def test_readme_script_text_is_escaped_in_html_report(self) -> None:
        claim = ClaimResult(
            kind="File path",
            claim="<script>alert(1)</script>",
            line=3,
            status="Unverified",
            evidence="The README text is not a confirmed repository path.",
            correction="Clarify whether the text is intended as a path.",
        )
        analysis = RepositoryAnalysis("safe & sound", (), "README.md", (claim,))
        html = build_html_report(analysis, summarize_claims(analysis.claims))
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<script>", html)


if __name__ == "__main__":
    unittest.main()
