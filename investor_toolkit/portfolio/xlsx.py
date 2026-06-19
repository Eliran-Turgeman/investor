from __future__ import annotations

import math
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


CellValue = str | int | float | bool | None

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def write_xlsx(path: str | Path, sheets: list[dict[str, Any]]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet_defs = [sheet for sheet in sheets if sheet.get("name")]
    if not sheet_defs:
        raise ValueError("Workbook must contain at least one sheet")

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(len(sheet_defs)))
        archive.writestr("_rels/.rels", _root_rels())
        archive.writestr("xl/workbook.xml", _workbook_xml(sheet_defs))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheet_defs)))
        for index, sheet in enumerate(sheet_defs, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _worksheet_xml(sheet.get("rows", []), sheet.get("widths", [])),
            )
    return output


def read_xlsx(path: str | Path) -> dict[str, list[list[Any]]]:
    workbook = Path(path)
    if not workbook.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook}")
    try:
        with zipfile.ZipFile(workbook, "r") as archive:
            shared_strings = _read_shared_strings(archive)
            workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = _read_workbook_relationships(archive)
            sheets: dict[str, list[list[Any]]] = {}
            for sheet in workbook_root.findall(f".//{{{MAIN_NS}}}sheet"):
                name = str(sheet.attrib.get("name", "")).strip()
                rel_id = sheet.attrib.get(f"{{{REL_NS}}}id")
                target = rels.get(rel_id or "")
                if not name or not target:
                    continue
                sheet_path = "xl/" + target.lstrip("/")
                sheet_path = sheet_path.replace("xl/xl/", "xl/")
                sheets[name] = _read_sheet(archive, sheet_path, shared_strings)
            return sheets
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid XLSX workbook: {workbook}") from exc
    except (KeyError, ET.ParseError) as exc:
        raise ValueError(f"Invalid XLSX workbook: {workbook}") from exc


def _content_types(sheet_count: int) -> str:
    sheet_overrides = "\n".join(
        f'  <Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '  <Default Extension="xml" ContentType="application/xml"/>\n'
        '  <Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
        f"{sheet_overrides}\n"
        "</Types>\n"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Relationships xmlns="{PACKAGE_REL_NS}">\n'
        '  <Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>\n'
        "</Relationships>\n"
    )


def _workbook_xml(sheets: list[dict[str, Any]]) -> str:
    sheet_lines = []
    for index, sheet in enumerate(sheets, start=1):
        name = _safe_sheet_name(str(sheet["name"]))
        sheet_lines.append(
            f'    <sheet name="{_escape_xml_attr(name)}" sheetId="{index}" r:id="rId{index}"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">\n'
        "  <sheets>\n"
        + "\n".join(sheet_lines)
        + "\n  </sheets>\n"
        "</workbook>\n"
    )


def _workbook_rels(sheet_count: int) -> str:
    rels = []
    for index in range(1, sheet_count + 1):
        rels.append(
            f'  <Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Relationships xmlns="{PACKAGE_REL_NS}">\n'
        + "\n".join(rels)
        + "\n</Relationships>\n"
    )


def _worksheet_xml(rows: list[list[CellValue]], widths: list[float]) -> str:
    cols = ""
    if widths:
        col_lines = []
        for index, width in enumerate(widths, start=1):
            if isinstance(width, (int, float)) and math.isfinite(float(width)) and width > 0:
                col_lines.append(
                    f'    <col min="{index}" max="{index}" width="{float(width):.1f}" customWidth="1"/>'
                )
        if col_lines:
            cols = "  <cols>\n" + "\n".join(col_lines) + "\n  </cols>\n"

    row_lines = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            cells.append(_cell_xml(row_index, column_index, value))
        row_lines.append(f'    <row r="{row_index}">' + "".join(cells) + "</row>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<worksheet xmlns="{MAIN_NS}">\n'
        f"{cols}"
        "  <sheetData>\n"
        + "\n".join(row_lines)
        + "\n  </sheetData>\n"
        "</worksheet>\n"
    )


def _cell_xml(row_index: int, column_index: int, value: CellValue) -> str:
    ref = f"{_column_name(column_index)}{row_index}"
    if value is None:
        return f'<c r="{ref}" t="inlineStr"><is><t></t></is></c>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        if math.isfinite(parsed):
            rendered = str(int(parsed)) if parsed.is_integer() else repr(parsed)
            return f'<c r="{ref}"><v>{rendered}</v></c>'
    text = escape(_sanitize_xml_text(str(value)), {'"': "&quot;"})
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall(f".//{{{MAIN_NS}}}si"):
        values.append("".join(item.itertext()))
    return values


def _read_workbook_relationships(archive: zipfile.ZipFile) -> dict[str, str]:
    root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rels = {}
    for rel in root.findall(f".//{{{PACKAGE_REL_NS}}}Relationship"):
        rels[str(rel.attrib.get("Id", ""))] = str(rel.attrib.get("Target", ""))
    return rels


def _read_sheet(
    archive: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
) -> list[list[Any]]:
    root = ET.fromstring(archive.read(sheet_path))
    rows: list[list[Any]] = []
    for row in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        values: list[Any] = []
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            ref = str(cell.attrib.get("r", ""))
            column_index = _column_index_from_ref(ref)
            while len(values) < column_index - 1:
                values.append(None)
            values.append(_read_cell(cell, shared_strings))
        while values and values[-1] in (None, ""):
            values.pop()
        rows.append(values)
    return rows


def _read_cell(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        inline = cell.find(f"{{{MAIN_NS}}}is")
        return "" if inline is None else "".join(inline.itertext())
    value = cell.find(f"{{{MAIN_NS}}}v")
    if value is None or value.text is None:
        return ""
    text = value.text
    if cell_type == "s":
        try:
            return shared_strings[int(text)]
        except (IndexError, ValueError):
            return ""
    if cell_type == "b":
        return text == "1"
    return _parse_number(text)


def _parse_number(value: str) -> Any:
    try:
        parsed = float(value)
    except ValueError:
        return value
    if not math.isfinite(parsed):
        return value
    return int(parsed) if parsed.is_integer() else parsed


def _safe_sheet_name(value: str) -> str:
    cleaned = re.sub(r"[\[\]\:\*\?\/\\]", " ", _sanitize_xml_text(value)).strip()
    return (cleaned or "Sheet")[:31]


def _sanitize_xml_text(value: str) -> str:
    chars = []
    for char in value:
        codepoint = ord(char)
        if (
            codepoint in (0x09, 0x0A, 0x0D)
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        ):
            chars.append(char)
        else:
            chars.append(" ")
    return "".join(chars)


def _escape_xml_attr(value: str) -> str:
    return escape(_sanitize_xml_text(value), {'"': "&quot;"})


def _column_name(index: int) -> str:
    name = ""
    current = index
    while current:
        current, remainder = divmod(current - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _column_index_from_ref(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref.upper())
    if not letters:
        return 1
    index = 0
    for char in letters.group(1):
        index = index * 26 + (ord(char) - 64)
    return index
