"""gathers figures and drafts a narrative.

Uses Gemini if the API key is set; falls back to the template otherwise.
"""

import json
import requests
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.programmes.models import Programme
from apps.reporting.services.kpis import (
    kpi_cascade,
    kpi_coverage,
    kpi_csat,
    kpi_pipeline,
    kpi_progression,
    kpi_reach,
    kpi_reach_by_session_region,
    kpi_reach_by_unit,
    suppress,
)


def gather_figures(programme=None, since=None):
    """Collect all KPI figures and breakdowns into a single dict."""
    from apps.reporting.services.kpis import (
        kpi_affiliation_split,
        kpi_cascade_top,
        kpi_coverage_by_unit,
        kpi_csat_distribution,
        kpi_evidence_by_type,
        kpi_pipeline_funnel,
        kpi_reach_by_category,
        kpi_reach_by_gender,
        kpi_reach_by_mode,
        kpi_reach_by_programme,
        kpi_reach_by_rank,
        kpi_reach_by_region,
        kpi_sessions_by_month,
    )

    figures = {
        # Headline KPIs
        "reach": kpi_reach(programme=programme, since=since),
        "coverage": kpi_coverage(programme=programme, since=since),
        "progression": kpi_progression(programme=programme, since=since),
        "csat": kpi_csat(programme=programme, since=since),
        "cascade": kpi_cascade(programme=programme),
        "pipeline": kpi_pipeline(programme=programme),
        "reach_by_unit": kpi_reach_by_unit(programme=programme, since=since),

        # Breakdowns
        "reach_by_gender": kpi_reach_by_gender(programme=programme, since=since),
        "reach_by_category": kpi_reach_by_category(programme=programme, since=since),
        "reach_by_rank": kpi_reach_by_rank(programme=programme, since=since),
        "reach_by_programme": kpi_reach_by_programme(since=since),
        "reach_by_mode": kpi_reach_by_mode(programme=programme, since=since),
        "reach_by_region": kpi_reach_by_region(programme=programme, since=since),
        "reach_by_session_region": kpi_reach_by_session_region(
            programme=programme, since=since
        ),
        "affiliation_split": kpi_affiliation_split(programme=programme, since=since),
        "csat_distribution": kpi_csat_distribution(programme=programme, since=since),
        "evidence_by_type": kpi_evidence_by_type(programme=programme, since=since),
        "sessions_by_month": kpi_sessions_by_month(programme=programme, since=since),
        "pipeline_funnel": kpi_pipeline_funnel(),
        "cascade_top": kpi_cascade_top(limit=10),
        "coverage_by_unit": kpi_coverage_by_unit(programme=programme),
    }

    return figures


def generate_memo(programme=None, period_label="", since=None, created_by=None):
    """Create a Memo row with figures and a narrative."""
    from apps.reporting.models import Memo

    figures = gather_figures(programme=programme, since=since)
    
    # Check if Gemini is being used
    used_ai = bool(getattr(settings, "GEMINI_API_KEY", ""))
    narrative = _generate_narrative(figures, period_label, programme)

    scope = {}
    if programme:
        scope["programme"] = programme.name
    if since:
        scope["since"] = since.isoformat()

    memo = Memo.objects.create(
        period_label=period_label or "Untitled period",
        scope=scope,
        figures=figures,
        narrative=narrative,
        used_ai=used_ai,  # <-- Now correctly reflects if Gemini was called
        version=1,
        created_by=created_by,
    )
    return memo


def _generate_narrative(figures, period_label, programme):
    """Generate a narrative. Uses Gemini if the API key is set; falls back to template."""
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    
    if not api_key:
        return _generate_narrative_template(figures, period_label, programme)
        
    try:
        return _generate_narrative_gemini(api_key, figures, period_label, programme)
    except Exception as e:
        print(f"⚠️ Gemini failed ({e}), falling back to template.")
        return _generate_narrative_template(figures, period_label, programme)


def _generate_narrative_gemini(api_key, figures, period_label, programme):
    """Call the Gemini API with the aggregated figures."""
    programme_name = programme.name if programme else "all programmes"
    
    prompt = f"""You are writing an executive memo for the Vice-Chancellor of KNUST.
Period: {period_label}
Scope: {programme_name}

Do NOT invent numbers. Use the aggregated data below.
Write clearly in UK English. Use these sections:
1. Executive summary (3 sentences)
2. Reach and coverage
3. Programme performance
4. Trainer pipeline
5. Recommendations (bullet points)

Data:
{json.dumps(figures, indent=2, default=str)}

Return only the narrative text. No markdown.
"""
    
    response = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "max_output_tokens": 1500},
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _generate_narrative_template(figures, period_label, programme):
    """Generate a rich, data-driven narrative from the breakdowns. No AI."""
    programme_name = programme.name if programme else "all programmes"
    period = period_label or "the reporting period"

    reach = figures["reach"]
    coverage = figures["coverage"]
    progression = figures["progression"]
    csat = figures["csat"]
    cascade = figures["cascade"]
    pipeline = figures["pipeline"]
    funnel = figures["pipeline_funnel"]

    lines = [
        "SUA+ PULSE EXECUTIVE REPORT",
        f"Period: {period}",
        f"Scope: {programme_name}",
        "",
        "=" * 60,
        "EXECUTIVE SUMMARY",
        "=" * 60,
        "",
    ]

    summary_parts = [
        f"Sua+ recorded {suppress(reach)} distinct people trained during {period}.",
    ]
    if coverage["percent"] is not None:
        summary_parts.append(
            f"Coverage across the university stands at {coverage['percent']}%, "
            f"with {coverage['numerator']} of {coverage['denominator']} people "
            f"having reached level 2 or above on at least one programme competency."
        )
    if csat["mean"] is not None:
        summary_parts.append(
            f"Participant satisfaction averaged {csat['mean']} out of 5."
        )
    if pipeline.get("Certified", 0) > 0:
        summary_parts.append(
            f"{suppress(pipeline['Certified'])} Certified Trainers are active "
            "across the university."
        )
    lines.append(" ".join(summary_parts))
    lines.append("")

    lines.append("=" * 60)
    lines.append("REACH AND COVERAGE")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Total distinct people reached: {suppress(reach)}")

    if figures["affiliation_split"]:
        lines.append("")
        lines.append("By affiliation:")
        for row in figures["affiliation_split"]:
            lines.append(f"  {row['affiliation'].title()}: {suppress(row['people'])}")

    if figures["reach_by_gender"]:
        lines.append("")
        lines.append("By gender:")
        for row in figures["reach_by_gender"]:
            lines.append(f"  {row['gender'].title()}: {suppress(row['people'])}")

    if figures["reach_by_category"]:
        lines.append("")
        lines.append("By staff/student category:")
        for row in figures["reach_by_category"]:
            lines.append(
                f"  {row['category'].replace('_', ' ').title()}: "
                f"{suppress(row['people'])}"
            )

    if figures["reach_by_rank"]:
        lines.append("")
        lines.append("By rank / level:")
        for row in figures["reach_by_rank"]:
            lines.append(f"  {row['rank']}: {suppress(row['people'])}")

    if figures["reach_by_unit"]:
        lines.append("")
        lines.append("By college / directorate:")
        for row in figures["reach_by_unit"]:
            lines.append(f"  {row['unit']}: {suppress(row['people'])}")

    if figures["coverage_by_unit"]:
        lines.append("")
        lines.append("Coverage by college (level 2+):")
        for row in figures["coverage_by_unit"]:
            lines.append(
                f"  {row['unit']}: {row['percent']}% "
                f"({suppress(row['numerator'])}/{row['denominator']})"
            )

    lines.append("")

    lines.append("=" * 60)
    lines.append("PROGRAMME PERFORMANCE")
    lines.append("=" * 60)
    lines.append("")

    if figures["reach_by_programme"]:
        lines.append("Reach by programme:")
        for row in figures["reach_by_programme"]:
            lines.append(f"  {row['programme']}: {suppress(row['people'])} people")

    if figures["reach_by_mode"]:
        lines.append("")
        lines.append("By delivery mode:")
        for row in figures["reach_by_mode"]:
            lines.append(
                f"  {row['mode'].replace('_', ' ').title()}: "
                f"{suppress(row['people'])}"
            )

    if figures["sessions_by_month"]:
        lines.append("")
        lines.append("Sessions delivered by month:")
        for row in figures["sessions_by_month"]:
            lines.append(f"  {row['month']}: {suppress(row['count'])}")

    if csat["mean"] is not None:
        dist = figures["csat_distribution"]
        lines.append("")
        lines.append(
            f"Satisfaction: {csat['mean']} / 5 "
            f"(from {suppress(dist['total'])} responses)"
        )
        lines.append("Distribution:")
        for score in [1, 2, 3, 4, 5]:
            n = dist["counts"][score]
            displayed = suppress(n) if n < 5 else n
            bar = "█" * n
            lines.append(f"  {score}: {bar} {displayed}")

    lines.append("")

    lines.append("=" * 60)
    lines.append("TRAINER PIPELINE")
    lines.append("=" * 60)
    lines.append("")

    stages = [
        ("People with evidence", funnel["with_evidence"]),
        ("Eligible", funnel["eligible"]),
        ("Nominated", funnel["nominated"]),
        ("Approved trainers", funnel["approved"]),
        ("Certified", funnel["certified"]),
        ("Master trainers", funnel["master"]),
    ]
    for label, value in stages:
        displayed = suppress(value) if value < 5 else value
        bar = "█" * value
        lines.append(f"  {label}: {bar} {displayed}")

    if figures["cascade_top"]:
        lines.append("")
        lines.append("Top trainers by cascade multiplier:")
        for row in figures["cascade_top"][:5]:
            lines.append(
                f"  {row['trainer']}: {row['multiplier']}× "
                f"({suppress(row['direct'])} direct + "
                f"{suppress(row['second_gen'])} second-gen)"
            )

    lines.append("")

    if figures["evidence_by_type"]:
        lines.append("=" * 60)
        lines.append("EVIDENCE TYPES")
        lines.append("=" * 60)
        lines.append("")
        for row in figures["evidence_by_type"]:
            lines.append(
                f"  {row['type'].replace('_', ' ').title()}: "
                f"{suppress(row['count'])}"
            )
        lines.append("")

    if figures["reach_by_region"] or figures.get("reach_by_session_region"):
        lines.append("=" * 60)
        lines.append("EXTERNAL REACH")
        lines.append("=" * 60)
        lines.append("")

        if figures["reach_by_region"]:
            lines.append("External participants by region:")
            for row in figures["reach_by_region"]:
                lines.append(
                    f"  {row['region']}: {suppress(row['people'])}"
                )

        if figures.get("reach_by_session_region"):
            lines.append("")
            lines.append("Off-campus sessions by region:")
            for row in figures["reach_by_session_region"]:
                lines.append(
                    f"  {row['region']}: {suppress(row['people'])}"
                )

        lines.append("")

    lines.append("=" * 60)
    lines.append("RECOMMENDATIONS")
    lines.append("=" * 60)
    lines.append("")
    recommendations = []

    if coverage["percent"] is not None and coverage["percent"] < 50:
        recommendations.append(
            f"- Coverage is below 50% ({coverage['percent']}%). "
            "Prioritise reaching more staff and students."
        )

    if pipeline.get("Certified", 0) == 0:
        recommendations.append(
            "- No Certified Trainers yet. Run the tier engine and approve nominations "
            "to build the trainer base."
        )

    if cascade["count"] == 0:
        recommendations.append(
            "- No cascade data. Certified Trainers must run their own sessions for "
            "the multiplier effect to show."
        )

    if figures["reach_by_gender"]:
        total = sum(r["people"] for r in figures["reach_by_gender"])
        if total >= 5:
            for r in figures["reach_by_gender"]:
                pct = round(r["people"] / total * 100, 1)
                if pct < 30:
                    recommendations.append(
                        f"- Gender balance: {r['gender']} represents only "
                        f"{pct}% of reach."
                    )
        else:
            recommendations.append(
                "- Gender breakdown is too small to report (under 5 records)."
            )

    if not recommendations:
        recommendations.append("- All measured indicators are within normal range.")

    lines.extend(recommendations)
    lines.append("")
    lines.append(f"Prepared by Sua+ · {period_label}")
    lines.append("")

    return "\n".join(lines)