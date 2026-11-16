"""CSV/XLSX schemas for Library import and export."""

import csv
import io
import json

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell

from apps.library.models import (
    CertificationCatalogue,
    CertificationCompetency,
    Competency,
    Crosswalk,
    Domain,
    EvidenceRule,
    Framework,
    Level,
    SurveyTemplate,
)


SCHEMAS = {
    "frameworks": (
        "code,name,publisher,version,source,is_knust,status".split(","),
        lambda: Framework.objects.order_by("code").values_list(
            "code", "name", "publisher", "version", "source", "is_knust", "status"
        ),
    ),
    "domains": (
        "framework_code,code,name,description,colour,display_order,status".split(","),
        lambda: Domain.objects.order_by("framework__code", "code").values_list(
            "framework__code", "code", "name", "description", "colour", "display_order", "status"
        ),
    ),
    "levels": (
        ["framework_code", "ordinal", "label", "descriptor"],
        lambda: Level.objects.order_by("framework__code", "ordinal").values_list(
            "framework__code", "ordinal", "label", "descriptor"
        ),
    ),
    "competencies": (
        "framework_code,domain_code,code,title,description,proficient_descriptor,status,replaced_by_code".split(","),
        lambda: Competency.objects.order_by("framework__code", "code").values_list(
            "framework__code", "domain__code", "code", "title", "description",
            "proficient_descriptor", "status", "replaced_by__code"
        ),
    ),
    "crosswalks": (
        ["knust_framework_code", "knust_competency_code", "external_framework_code", "external_competency_code", "external_level_ordinal", "note"],
        lambda: Crosswalk.objects.order_by("knust_competency__code", "external_competency__code").values_list(
            "knust_competency__framework__code", "knust_competency__code",
            "external_competency__framework__code", "external_competency__code",
            "external_level__ordinal", "note"
        ),
    ),
    "evidence_rules": (
        ["evidence_type", "framework_code", "competency_code", "max_level", "description", "status"],
        lambda: EvidenceRule.objects.order_by("evidence_type", "competency__code").values_list(
            "evidence_type", "competency__framework__code", "competency__code",
            "max_level", "description", "status"
        ),
    ),
    "certifications": (
        ["issuer", "title", "verification_method", "renewal_months", "active", "auto_approve"],
        lambda: CertificationCatalogue.objects.order_by("issuer", "title").values_list(
            "issuer", "title", "verification_method", "renewal_months", "active", "auto_approve"
        ),
    ),
    "certification_competencies": (
        ["issuer", "certification_title", "framework_code", "competency_code", "max_level"],
        lambda: CertificationCompetency.objects.order_by("certification__issuer", "certification__title", "competency__code").values_list(
            "certification__issuer", "certification__title", "competency__framework__code",
            "competency__code", "max_level"
        ),
    ),
    "surveys": (
        ["name", "type", "version", "is_default", "status", "definition_json"],
        lambda: SurveyTemplate.objects.order_by("type", "name").values_list(
            "name", "type", "version", "is_default", "status", "definition"
        ),
    ),
}

IMPORTABLE_SHEETS = {
    "frameworks", "domains", "levels", "competencies", "crosswalks", "evidence_rules"
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 10_000


def _cell_value(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    text = str(value)
    # Keep exported spreadsheet data inert when opened in Excel.
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def export_csv(entity):
    if entity not in SCHEMAS:
        raise ValueError("Choose a supported Library data set.")
    headers, rows_factory = SCHEMAS[entity]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows_factory():
        writer.writerow([_cell_value(value) for value in row])
    return output.getvalue().encode("utf-8-sig")


def export_xlsx():
    workbook = Workbook(write_only=True)
    for entity, (headers, rows_factory) in SCHEMAS.items():
        sheet = workbook.create_sheet(title=entity[:31])
        sheet.append(headers)
        for row in rows_factory():
            safe_row = []
            for value in row:
                cell = WriteOnlyCell(sheet, value=_cell_value(value))
                cell.data_type = "s"
                safe_row.append(cell)
            sheet.append(safe_row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _rows_from_csv(upload, entity):
    if entity not in IMPORTABLE_SHEETS:
        raise ValueError("CSV import is supported for frameworks, domains, levels, competencies, crosswalks, and evidence rules.")
    headers, _rows = SCHEMAS[entity]
    text = upload.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != headers:
        raise ValueError(f"CSV headers must match the {entity} export format.")
    rows = list(reader)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"A maximum of {MAX_ROWS} rows can be imported at once.")
    return {entity: rows}


def _rows_from_xlsx(upload):
    workbook = load_workbook(upload, read_only=True, data_only=True)
    result = {}
    total = 0
    for sheet in workbook.worksheets:
        entity = sheet.title.lower()
        if entity not in IMPORTABLE_SHEETS:
            continue
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows, None)
        expected, _rows = SCHEMAS[entity]
        if list(headers or []) != expected:
            raise ValueError(f"The {sheet.title} sheet does not match the Library export format.")
        values = []
        for row in rows:
            if not any(value not in (None, "") for value in row):
                continue
            values.append({key: value for key, value in zip(expected, row)})
        total += len(values)
        result[entity] = values
    workbook.close()
    if not result:
        raise ValueError("The workbook contains no supported Library data sheets.")
    if total > MAX_ROWS:
        raise ValueError(f"A maximum of {MAX_ROWS} rows can be imported at once.")
    return result


def parse_upload(upload, entity=""):
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValueError("The upload must be 10 MB or smaller.")
    name = upload.name.lower()
    if name.endswith(".csv"):
        return _rows_from_csv(upload, entity)
    if name.endswith(".xlsx"):
        return _rows_from_xlsx(upload)
    raise ValueError("Choose a .csv or .xlsx file.")
