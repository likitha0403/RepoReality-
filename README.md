RepoReality

Evidence-Driven Documentation Verification for Open-Source Repositories

RepoReality is a Python-based tool that helps maintainers identify potential inconsistencies between repository documentation and the files available in a project archive.

Documentation can become outdated as code changes. Installation commands may refer to missing files, documented paths may no longer exist, and runtime requirements may lack supporting evidence. RepoReality analyzes documentation claims against inspected repository contents and presents findings for human review.

The Problem

Documentation drift makes open-source projects harder to install, understand, maintain, and contribute to. Manually checking every documented path and setup instruction can be time-consuming.

What RepoReality Does

- Documentation verification: Checks supported documentation claims against repository evidence.
- Evidence-backed results: Classifies findings as "Verified", "Failed", or "Unverified".
- Installation checks: Checks whether required files, such as "requirements.txt", exist for supported installation commands.
- Repository analysis: Inspects archive contents and supported documentation references.
- Correction guidance: Provides explanations and suggested corrections for findings that need attention.
- Impact and recovery support: Provides repository analysis and recovery-oriented information through the available analysis modules.
- Reviewable output: Presents findings in a Streamlit interface and supports report generation.

How It Works

1. Upload a repository ZIP archive.
2. RepoReality inspects the archive and its supported contents.
3. It extracts supported documentation claims and checks them against available evidence.
4. Findings are categorized by verification status, with explanations and suggested corrections where applicable.
5. Review the results and decide which documentation or repository changes are appropriate.

Understanding the Results

Status| Meaning
Verified| Supporting evidence was found for the check performed.
Failed| The available evidence conflicts with the documented claim or does not satisfy the applicable check.
Unverified| There is insufficient evidence to make a reliable determination.

These results describe the checks performed; they do not guarantee that an entire repository or application is correct.

Security Considerations

RepoReality is designed to inspect uploaded archives without executing uploaded repository code by default.

Archive handling includes safeguards intended to address unsafe paths, duplicate filenames, corrupted archive entries, and potentially dangerous archive contents. These safeguards reduce risk but should not be interpreted as a guarantee against every malicious upload.

Only upload repositories you are authorized to inspect. Review generated recommendations before applying changes.

Technology Stack

- Python
- Streamlit
- Pandas
- Plotly
- Python standard library testing tools

Getting Started

Requirements

- Python compatible with the dependencies in "requirements.txt"
- pip
- Git (optional, for cloning the repository)

Installation

Clone the repository:

git clone https://github.com/mysorequeen64-ops/RepoReality.git
cd RepoReality

Create and activate a virtual environment.

Windows PowerShell:

python -m venv .venv
.\.venv\Scripts\Activate.ps1

Install dependencies:

python -m pip install -r requirements.txt

Run the Application

python -m streamlit run app.py

Open the local address displayed by Streamlit in your browser.

Testing

Run the test suite:

python -m unittest discover -v

Check Python syntax and compilation:

python -m compileall -q app.py reporeality tests

Check Git whitespace errors:

git diff --check

Tests help verify implemented behavior, but passing tests do not establish that every possible input or security scenario is covered.

Project Structure

RepoReality/
├── app.py
├── reporeality/
│   ├── analyzer.py
│   ├── impact.py
│   ├── preview.py
│   ├── repomap.py
│   └── report.py
├── tests/
├── requirements.txt
├── .gitignore
├── LICENSE
└── README.md

GitHub Copilot and Development

GitHub Copilot was used as a development assistant during project planning, implementation, debugging, and test development.

Examples of relevant development work include refining documentation-checking rules, investigating incorrect findings, improving archive-handling behavior, and adding regression tests.

Copilot-assisted changes should still be reviewed and tested. AI-generated suggestions are not treated as proof that a finding is correct; RepoReality presents evidence and explanations to support human review.

Current Limitations

- Verification depends on the documentation patterns and evidence the current implementation supports.
- A missing file does not always mean the documentation is wrong; local virtual environments and generated files may not belong in a repository.
- Some claims may remain unverified when the required evidence is unavailable.
- Suggested corrections require human review.
- The tool does not guarantee complete documentation correctness, application correctness, or comprehensive security.

Future Improvements

Potential improvements include broader manifest support, more robust command validation, additional regression tests, and integration with pull-request review workflows.

License

This project is licensed under the MIT License. See "LICENSE" (LICENSE) for details.

Repository

Source code and tests: https://github.com/mysorequeen64-ops/RepoReality
