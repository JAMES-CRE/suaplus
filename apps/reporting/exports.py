"""Download helpers for filtered reporting data."""

import csv
import io
import json
import textwrap
from datetime import date

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from apps.reporting.models import ExportLog

HEADERS = ("Section", "Metric", "Value")
COUNT_FIELDS = {
    "reach", "workshops_held", "active_trainers", "count", "people", "total",
    "numerator", "denominator", "with_evidence", "eligible", "nominated",
    "approved", "certified", "master", "direct", "second_gen", "responses",
}
DERIVED_FIELDS = {"percent", "mean", "mean_delta", "mean_multiplier"}


def suppress_small_counts(value, field="", threshold=5):
    """Redact counts under five and aggregates derived from small groups."""
    if isinstance(value, dict):
        count_values = [
            child for key, child in value.items()
            if key in COUNT_FIELDS and isinstance(child, (int, float))
        ]
        small_group = any(0 <= child < threshold for child in count_values)
        if field == "pipeline":
            small_group = any(
                isinstance(child, (int, float)) and 0 <= child < threshold
                for child in value.values()
            )
        result = {
            key: suppress_small_counts(child, key, threshold)
            for key, child in value.items()
            if key != "chart_value"
        }
        if field == "pipeline":
            result = {
                key: "<5" if isinstance(child, (int, float)) and 0 <= child < threshold else child
                for key, child in result.items()
            }
        if small_group:
            for key in DERIVED_FIELDS:
                if key in result and result[key] is not None:
                    result[key] = "<5"
        if field == "ai_coverage":
            chart_count = value.get("people")
            result["chart_value"] = (
                None if isinstance(chart_count, (int, float)) and 0 <= chart_count < threshold
                else result.get("percent")
            )
            return result
        if field not in {"reach_by_unit", "monthly_reach"}:
            return result
        chart_count = value.get("people", value.get("count"))
        if chart_count is not None:
            result["chart_value"] = None if 0 <= chart_count < threshold else chart_count
        return result
    if isinstance(value, list):
        return [suppress_small_counts(item, field, threshold) for item in value]
    if field in COUNT_FIELDS and isinstance(value, (int, float)) and 0 <= value < threshold:
        return "<5"
    return value


def dashboard_rows(data):
    """Flatten aggregate dashboard values into rows suitable for download."""
    rows = []

    def add_value(section, metric, value):
        if isinstance(value, dict):
            for key, child in value.items():
                add_value(section, f"{metric} · {key}", child)
        elif isinstance(value, list):
            for index, child in enumerate(value, start=1):
                if isinstance(child, dict):
                    labels = [
                        str(child[key]) for key in (
                            "unit", "college", "month", "programme", "category",
                            "region", "gender", "affiliation", "mode", "rank", "type",
                        ) if child.get(key) not in (None, "")
                    ]
                    label = " · ".join(labels) or str(index)
                    label_keys = {
                        "unit", "college", "month", "programme", "category", "region",
                        "gender", "affiliation", "mode", "rank", "type", "chart_value",
                    }
                    for key, child_value in child.items():
                        if key not in label_keys:
                            add_value(section, f"{metric} · {label} · {key}", child_value)
                else:
                    add_value(section, f"{metric} · {index}", child)
        else:
            rows.append((section, metric, value))

    for key, value in data.items():
        section = "KPI" if key in {"reach", "workshops_held", "active_trainers", "cascade", "csat", "baseline", "coverage", "progression", "pipeline"} else "Breakdown"
        add_value(section, key.replace("_", " ").title(), value)
    return rows


def _safe_cell(value):
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return value
    value = str(value)
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def _pdf_response(rows, filename):
    """Create a small, dependency-free PDF table for the current export."""
    lines = ["Sua+ Pulse Dashboard Export", f"Generated: {date.today().isoformat()}", ""]
    lines.append(" | ".join(HEADERS))
    lines.append("-" * 88)
    for row in rows:
        value = row[2]
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        line = " | ".join(str(part if part is not None else "") for part in (row[0], row[1], value))
        lines.extend(textwrap.wrap(line, width=100, break_long_words=True, break_on_hyphens=False) or [""])

    pages = [lines[index:index + 52] for index in range(0, len(lines), 52)] or [[]]
    objects = [b"", b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_ids = []
    for page_number, page_lines in enumerate(pages, start=1):
        page_id = len(objects)
        content_id = page_id + 1
        page_ids.append(page_id)
        commands = ["BT", "/F1 9 Tf", "45 795 Td", "12 TL"]
        for line in page_lines:
            safe_line = line.encode("latin-1", "replace").decode("latin-1")
            safe_line = safe_line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({safe_line}) Tj T* ")
        commands.extend([f"(Page {page_number} of {len(pages)}) Tj", "ET"])
        stream = "\n".join(commands).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii")
        )
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
    objects[2] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_ids)} >>".encode("ascii")
    )

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, body in enumerate(objects[1:], start=1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    response = HttpResponse(bytes(output), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response


def export_report(rows, *, export_format, actor, view_name, filters=None):
    """Return a CSV, XLSX, or PDF response and write its audit record."""
    from django.core.exceptions import PermissionDenied

    from apps.accounts.permissions import can
    if not can(actor, "export_reports"):
        raise PermissionDenied
    if export_format not in {"csv", "xlsx", "pdf"}:
        raise ValueError("Choose CSV, XLSX, or PDF export.")
    rows = list(rows)
    filename = f"suaplus-{view_name}-{date.today().isoformat()}"

    if export_format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(HEADERS)
        writer.writerows([_safe_cell(value) for value in row] for row in rows)
        response = HttpResponse(output.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    elif export_format == "xlsx":
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Dashboard")
        sheet.append(HEADERS)
        for row in rows:
            safe_row = []
            for value in row:
                cell = WriteOnlyCell(sheet, value=_safe_cell(value))
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                safe_row.append(cell)
            sheet.append(safe_row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    else:
        response = _pdf_response(rows, filename)

    ExportLog.objects.create(
        actor=actor,
        view_name=view_name,
        filters=filters or {},
        format=export_format,
        row_count=len(rows),
    )
    return response
