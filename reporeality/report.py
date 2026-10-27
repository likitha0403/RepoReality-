"""Generate self-contained, printable HTML verification reports."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from .analyzer import RepositoryAnalysis, summarize_claims
from .impact import assess_integrity_impact


STATUS_CLASSES = {
    "Verified": "verified",
    "Failed": "failed",
    "Unverified": "unverified",
}
GUIDANCE_FIELDS = (
    "README says",
    "Rule checked",
    "What we found",
    "Why it is flagged",
    "Type",
    "Suggested fix",
)


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def build_html_report(
    analysis: RepositoryAnalysis,
    summary: dict[str, int] | None = None,
    generated_at: str | None = None,
) -> str:
    """Build a standalone HTML report without external assets or executable code."""
    counts = summary or summarize_claims(analysis.claims)
    date_text = generated_at or datetime.now(timezone.utc).isoformat()
    integrity_impact = assess_integrity_impact(analysis)

    integrity_issues = sorted(
        analysis.integrity_issues,
        key=lambda issue: (
            0 if issue.category.casefold() == "corrupt" else 1,
            issue.path.casefold(),
            issue.problem_type.casefold(),
        ),
    )
    integrity_rows = "".join(
        "<tr>"
        f"<td>{_text(issue.path)}</td>"
        f"<td>{_text(issue.problem_type)}</td>"
        f"<td>{_text(issue.details)}</td>"
        "</tr>"
        for issue in integrity_issues
    )
    if not integrity_rows:
        integrity_rows = '<tr><td colspan="3">No archive integrity findings were recorded.</td></tr>'

    impact_blocks = []
    for item in integrity_impact:
        references = item["references"]
        reference_list = "".join(
            f"<li>{_text(reference['path'])}, line {_text(reference['line'])}: "
            f"<code>{_text(reference['text'])}</code></li>"
            for reference in references
        ) or "<li>No matching references were found in readable decoded text.</li>"
        affected = "".join(
            f"<li>{_text(check['source'])}, line {_text(check['line'])} — "
            f"{_text(check['kind'])} {_text(check['claim'])} "
            f"({_text(check['status'])}): {_text(check['evidence'])}</li>"
            for check in item["affected_checks"]
        ) or "<li>No directly affected verification check was identified from the recorded results.</li>"
        impact_blocks.append(
            "<article class=\"guidance-card\">"
            f"<h3>{_text(item['path'])}</h3>"
            f"<p><strong>Integrity Error:</strong> {_text(item['error_type'])} — {_text(item['details'])}</p>"
            f"<p><strong>Evidence:</strong> {_text(item['what_is_confirmed'])}</p>"
            f"<p><strong>Potential Impact:</strong> {_text(item['potential_impact'])}</p>"
            f"<p><strong>Affected Checks:</strong></p><ul>{affected}</ul>"
            f"<p><strong>Repository references:</strong></p><ul>{reference_list}</ul>"
            f"<p><strong>Recommended Recovery:</strong> {_text(item['recovery'])}</p>"
            f"<p><strong>Limitations:</strong> {_text(item['limitations'])}</p>"
            "</article>"
        )
    impact_section = "".join(impact_blocks) or "<p>No unreadable archive entries require recovery guidance.</p>"

    document_rows = "".join(
        "<tr>"
        f"<td>{_text(document.path)}</td>"
        f"<td>{_text(document.status)}</td>"
        f"<td>{_text(document.claim_count)}</td>"
        f"<td>{_text(document.reason)}</td>"
        "</tr>"
        for document in analysis.documents_checked
    )
    if not document_rows:
        document_rows = '<tr><td colspan="4">No eligible documentation files were selected.</td></tr>'

    result_rows = "".join(
        "<tr>"
        f"<td><span class=\"badge {STATUS_CLASSES.get(claim.status, 'unverified')}\">{_text(claim.status)}</span></td>"
        f"<td>{_text(claim.source_document)}</td>"
        f"<td>{_text(claim.line)}</td>"
        f"<td class=\"wrapped\">{_text(claim.claim)}</td>"
        f"<td class=\"wrapped\">{_text(claim.evidence)}</td>"
        "</tr>"
        for claim in analysis.claims
    )
    if not result_rows:
        result_rows = '<tr><td colspan="5">No documentation claims were detected.</td></tr>'

    guidance_blocks = []
    for claim in analysis.claims:
        if claim.status not in {"Failed", "Unverified"}:
            continue
        guidance = claim.guidance()
        fields = "".join(
            "<div class=\"guidance-field\">"
            f"<dt>{_text(label)}</dt><dd>{_text(guidance[label])}</dd>"
            "</div>"
            for label in GUIDANCE_FIELDS
        )
        guidance_blocks.append(
            "<article class=\"guidance-card\">"
            f"<h3><span class=\"badge {STATUS_CLASSES.get(claim.status, 'unverified')}\">{_text(claim.status)}</span> "
            f"{_text(claim.source_document)} — line {_text(claim.line)}</h3>"
            f"<dl>{fields}</dl>"
            "</article>"
        )
    if not guidance_blocks:
        guidance_blocks.append("<p>No Failed or Unverified claims require follow-up.</p>")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RepoReality report — {_text(analysis.repository_name)}</title>
<style>
:root {{ color-scheme: light; font-family: Arial, Helvetica, sans-serif; color: #17212b; background: #f4f7fb; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 2rem; }}
main {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ margin: 0 0 .4rem; font-size: 2rem; }}
h2 {{ margin: 2rem 0 .7rem; font-size: 1.25rem; border-bottom: 1px solid #d5dde7; padding-bottom: .45rem; }}
h3 {{ margin: 0 0 .8rem; font-size: 1rem; }}
.generated {{ color: #5b6876; margin-bottom: 1.5rem; }}
.summary {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .75rem; }}
.metric {{ padding: 1rem; background: #fff; border: 1px solid #dce3eb; border-radius: 9px; }}
.metric strong {{ display: block; font-size: 1.5rem; }}
.metric span {{ color: #536170; font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; }}
.badge {{ display: inline-block; padding: .2rem .55rem; border-radius: 999px; font-size: .78rem; font-weight: 700; white-space: nowrap; }}
.verified {{ color: #176b35; background: #dff4e5; }}
.failed {{ color: #a3211b; background: #fde4e2; }}
.unverified {{ color: #805900; background: #fff1c2; }}
.note {{ padding: .8rem 1rem; background: #fff8df; border-left: 4px solid #d29922; border-radius: 4px; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #dce3eb; }}
th, td {{ text-align: left; vertical-align: top; padding: .65rem .75rem; border-bottom: 1px solid #e3e8ef; }}
th {{ background: #edf2f7; font-size: .78rem; }}
.wrapped {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
.guidance-card {{ background: #fff; border: 1px solid #dce3eb; border-radius: 9px; padding: 1rem; margin: .8rem 0; break-inside: avoid; }}
.guidance-card dl {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .6rem; margin: 0; }}
.guidance-field {{ background: #f7f9fc; padding: .7rem; border-radius: 6px; min-width: 0; }}
.guidance-field dt {{ color: #536170; font-size: .72rem; font-weight: 700; text-transform: uppercase; margin-bottom: .3rem; }}
.guidance-field dd {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.45; }}
footer {{ margin-top: 2rem; padding-top: .8rem; border-top: 1px solid #d5dde7; color: #536170; font-size: .85rem; }}
@media (max-width: 700px) {{ body {{ padding: 1rem; }} .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .guidance-card dl {{ grid-template-columns: 1fr; }} }}
@media print {{
  body {{ padding: 0; background: #fff; }}
  main {{ max-width: none; }}
  h2 {{ break-after: avoid; }}
  tr {{ break-inside: avoid; }}
  .metric, .guidance-card, table {{ box-shadow: none; }}
  @page {{ margin: 16mm; }}
}}
</style>
</head>
<body>
<main>
<header>
<h1>RepoReality Documentation Verification Report</h1>
<p><strong>Repository:</strong> {_text(analysis.repository_name)}</p>
<p class="generated">Generated: {_text(date_text)}</p>
</header>
<section>
<h2>Summary</h2>
<div class="summary">
<div class="metric"><span>Total checks</span><strong>{_text(counts.get("total", 0))}</strong></div>
<div class="metric"><span>Verified</span><strong>{_text(counts.get("verified", 0))}</strong></div>
<div class="metric"><span>Failed</span><strong>{_text(counts.get("failed", 0))}</strong></div>
<div class="metric"><span>Unverified</span><strong>{_text(counts.get("unverified", 0))}</strong></div>
</div>
</section>
<section>
<h2>Archive Integrity</h2>
<p class="note">Incomplete evidence is not proof that the repository is valid.</p>
<div class="table-wrap"><table><thead><tr><th>Path</th><th>Problem</th><th>Details</th></tr></thead>
<tbody>{integrity_rows}</tbody></table></div>
<h3>Impact &amp; Recovery</h3>
{impact_section}
</section>
<section>
<h2>Documents checked</h2>
<div class="table-wrap"><table><thead><tr><th>Document</th><th>Status</th><th>Claims</th><th>Reason</th></tr></thead>
<tbody>{document_rows}</tbody></table></div>
</section>
<section>
<h2>Results</h2>
<div class="table-wrap"><table><thead><tr><th>Status</th><th>Source</th><th>README line</th><th>Claim</th><th>Evidence</th></tr></thead>
<tbody>{result_rows}</tbody></table></div>
</section>
<section>
<h2>Why and how to fix</h2>
{"".join(guidance_blocks)}
</section>
<footer>This check is deterministic and uses only the verification rules and evidence available to RepoReality.</footer>
</main>
</body>
</html>"""
