from __future__ import annotations

import os
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph


MODEL_BLUE = RGBColor(0x00, 0x6D, 0x9F)
DARK_TEXT = RGBColor(0x1F, 0x38, 0x62)
MUTED_TEXT = RGBColor(0x5C, 0x66, 0x70)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _textbox_text(box: Any) -> str:
    return "".join(node.text or "" for node in box.iter(qn("w:t")))


def _set_textbox_text(box: Any, value: str) -> None:
    nodes = list(box.iter(qn("w:t")))
    if not nodes:
        return
    nodes[0].text = value
    for node in nodes[1:]:
        node.text = ""


def _replace_textbox_jockey_client(box: Any, client: str) -> None:
    nodes = list(box.iter(qn("w:t")))
    for index, node in enumerate(nodes):
        if (node.text or "").strip() == "JOCKEY":
            node.text = client
            for cleanup in nodes[index + 1 : index + 4]:
                if (cleanup.text or "").strip() in {"", "SALUD"}:
                    cleanup.text = ""
            return


def _set_cover_line(paragraph: Paragraph, text: str) -> None:
    paragraph.text = ""
    run = paragraph.add_run(text)
    run.font.name = "Lucida Sans Unicode"
    run.font.size = Pt(14)
    run.font.color.rgb = DARK_TEXT


def update_cover_and_footer(document: Document, payload: dict[str, Any]) -> None:
    client = _clean_text(payload.get("client_name")) or "Cliente"
    period = _clean_text(payload.get("period")) or "Periodo no especificado"
    prepared_by = _clean_text(payload.get("prepared_by")) or "TXDXSECURE"

    for box in document._element.xpath(".//w:txbxContent"):
        original = _textbox_text(box).strip()
        if original == "Change for date":
            _set_textbox_text(box, period)
        elif original == "Change for prepared for":
            _set_textbox_text(box, client)
        elif original == "TXDXSECURE":
            _set_textbox_text(box, prepared_by)
        elif "JOCKEY" in original and "SALUD" in original:
            _replace_textbox_jockey_client(box, client)

    for paragraph in document.paragraphs[:45]:
        text = paragraph.text.strip()
        if "JOCKEY SALUD" in text and "TXDXSECURE" in text:
            _set_cover_line(paragraph, f"{client}\t{prepared_by}")
        elif "Del 20 de junio al 26 de junio del 2026" in text or "Del 20 al 26 de junio del 2026" in text:
            _set_cover_line(paragraph, period)

    for section in document.sections:
        footer = section.footer
        footer.is_linked_to_previous = False
        if not footer.paragraphs:
            footer.add_paragraph()
        paragraph = footer.paragraphs[0]
        paragraph.text = ""
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.tab_stops.clear_all()
        paragraph.paragraph_format.tab_stops.add_tab_stop(Inches(2.9))
        paragraph.paragraph_format.tab_stops.add_tab_stop(Inches(6.15))
        for index, text in enumerate(("TxdxSecure", "Minority Report XOC", client)):
            if index:
                paragraph.add_run("\t")
            run = paragraph.add_run(text)
            run.font.size = Pt(8.5)
            run.font.color.rgb = MUTED_TEXT


def clear_template_body_after_cover(document: Document) -> None:
    body = document._element.body
    children = list(body)
    preserve_until = -1
    for index, child in enumerate(children):
        text = "".join(node.text or "" for node in child.iter(qn("w:t"))).strip()
        if text == "Contenido" or text.startswith("Contenido"):
            preserve_until = index - 1
            while preserve_until >= 0:
                previous = children[preserve_until]
                has_section_break = bool(previous.findall(".//" + qn("w:sectPr")))
                text_before = "".join(node.text or "" for node in previous.iter(qn("w:t"))).strip()
                if has_section_break or text_before:
                    break
                preserve_until -= 1
            break
    if preserve_until < 0:
        last_drawing_index = -1
        for index, child in enumerate(children):
            if child.findall(".//" + qn("w:drawing")):
                last_drawing_index = index
        preserve_until = last_drawing_index if last_drawing_index >= 0 else -1
    preserved = set(children[: preserve_until + 1])
    for child in list(body):
        if child not in preserved and child.tag != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sectPr":
            body.remove(child)


def _style_name(document: Document, *names: str) -> str:
    for name in names:
        if name in document.styles:
            return name
    return names[-1]


def apply_example_body_style(document: Document) -> None:
    styles = document.styles
    if "Body Text" not in styles:
        styles.add_style("Body Text", WD_STYLE_TYPE.PARAGRAPH)
    if "Normal" in styles:
        normal = styles["Normal"].font
        normal.name = "Tahoma"
        normal.size = Pt(10)
    if "Heading 1" in styles:
        font = styles["Heading 1"].font
        font.name = "Cambria"
        font.size = Pt(16)
        font.bold = True
    if "Heading 2" in styles:
        font = styles["Heading 2"].font
        font.name = "Arial Black"
        font.size = Pt(12)
        font.bold = True
        font.color.rgb = MODEL_BLUE
    if "Heading 3" in styles:
        font = styles["Heading 3"].font
        font.name = "Arial Black"
        font.size = Pt(10.5)
        font.bold = True
    if "Body Text" in styles:
        body = styles["Body Text"]
        body.font.name = "Tahoma"
        body.font.size = Pt(10)
        body.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        body.paragraph_format.space_after = Pt(6)


def _spacing(paragraph: Any, *, before: float = 0, after: float = 7) -> None:
    paragraph.paragraph_format.space_before = Pt(before)
    paragraph.paragraph_format.space_after = Pt(after)


def _format_run(run: Any, *, bold: bool = False, italic: bool = False, color: RGBColor | None = None, size: float | None = None) -> None:
    run.bold = bold or None
    run.italic = italic or None
    if color:
        run.font.color.rgb = color
    if size:
        run.font.size = Pt(size)


def add_heading(document: Document, title: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.style = _style_name(document, "Heading 2", "Normal")
    _spacing(paragraph, before=12, after=6)
    run = paragraph.add_run(title)
    _format_run(run, color=MODEL_BLUE)


def add_subheading(document: Document, title: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.style = _style_name(document, "Heading 3", "Normal")
    _spacing(paragraph, before=7, after=4)
    run = paragraph.add_run(title)
    _format_run(run)


def add_body(document: Document, text: Any) -> None:
    chunks = [chunk.strip() for chunk in str(text or "").splitlines() if chunk.strip()]
    for chunk in chunks:
        paragraph = document.add_paragraph()
        paragraph.style = _style_name(document, "Body Text", "Normal")
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _spacing(paragraph, after=5)
        paragraph.add_run(chunk)


def add_bullets(document: Document, values: Any) -> None:
    for value in values or []:
        text = _clean_text(value)
        if not text:
            continue
        paragraph = document.add_paragraph()
        paragraph.style = _style_name(document, "List Paragraph", "Body Text", "Normal")
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _spacing(paragraph, after=4)
        paragraph.add_run(text)


def _shade_cell(cell: Any, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), fill)


def _set_cell_border(cell: Any, color: str = "BFBFBF", size: str = "4") -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right"):
        node = borders.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), size)
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), color)


def _set_cell_text(cell: Any, text: Any, *, bold: bool = False, color: RGBColor | None = None, size: float = 9.2) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    _spacing(paragraph, after=2)
    run = paragraph.add_run(str(text or ""))
    _format_run(run, bold=bold, color=color, size=size)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def add_key_value_table(document: Document, rows: list[tuple[str, Any]]) -> None:
    table = document.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, header in zip(table.rows[0].cells, ("Campo", "Detalle")):
        _shade_cell(cell, "006D9F")
        _set_cell_border(cell)
        _set_cell_text(cell, header, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
    for key, value in rows:
        cells = table.add_row().cells
        for cell in cells:
            _set_cell_border(cell)
        _set_cell_text(cells[0], key, bold=True, color=DARK_TEXT)
        _set_cell_text(cells[1], value)
    document.add_paragraph()


def add_findings_table(document: Document, findings: list[dict[str, Any]]) -> None:
    if not findings:
        return
    table = document.add_table(rows=1, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, header in zip(table.rows[0].cells, ("ID", "Vulnerabilidad", "Hosts Afectados", "Severidad")):
        _shade_cell(cell, "006D9F")
        _set_cell_border(cell)
        _set_cell_text(cell, header, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF), size=8.8)
    for finding in findings:
        cells = table.add_row().cells
        for cell in cells:
            _set_cell_border(cell)
        _set_cell_text(cells[0], finding.get("id"), color=DARK_TEXT, size=8.5)
        _set_cell_text(cells[1], finding.get("vulnerability"), size=8.5)
        _set_cell_text(cells[2], finding.get("affected_hosts"), size=8.5)
        _set_cell_text(cells[3], finding.get("severity"), bold=True, size=8.5)
    document.add_paragraph()


def enable_update_fields_on_open(document: Document) -> None:
    settings = document.settings.element
    update_fields = settings.find(qn("w:updateFields"))
    if update_fields is None:
        update_fields = OxmlElement("w:updateFields")
        settings.append(update_fields)
    update_fields.set(qn("w:val"), "true")


def add_word_toc_field(document: Document) -> None:
    paragraph = document.add_paragraph()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    paragraph.add_run()._r.append(begin)
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = ' TOC \\o "1-3" \\h \\z \\u '
    paragraph.add_run()._r.append(instruction)
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    paragraph.add_run()._r.append(separate)
    placeholder = paragraph.add_run("Indice automatico. Al abrir el documento en Word, actualice los campos si no se muestran las paginas.")
    placeholder.italic = True
    placeholder.font.size = Pt(9)
    placeholder.font.color.rgb = MUTED_TEXT
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    paragraph.add_run()._r.append(end)


def build_report_body(document: Document, payload: dict[str, Any]) -> None:
    enable_update_fields_on_open(document)
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    title.style = _style_name(document, "Heading 1", "Normal")
    _spacing(title, before=8, after=10)
    title.add_run("Contenido")
    add_word_toc_field(document)
    document.add_page_break()

    add_heading(document, "1. Datos generales")
    add_subheading(document, "1.1 Servicio de Monitoreo")
    add_body(document, payload.get("service_name"))
    add_subheading(document, "1.2 Periodo")
    add_body(document, payload.get("period"))
    add_subheading(document, "1.3 Herramientas")
    add_bullets(document, [f"{tool.get('name')}: {tool.get('description')}" for tool in payload.get("tools", [])])
    add_subheading(document, "1.4 Datos Base")
    add_body(document, payload.get("data_base"))

    add_heading(document, "2. Resumen ejecutivo del dominio")
    add_body(document, payload.get("executive_summary"))
    add_subheading(document, "2.1 Analisis Comparativo de Vulnerabilidades Semanales")
    add_body(document, (payload.get("vulnerability_comparison") or {}).get("summary"))
    add_subheading(document, "2.2 Histograma de la seguridad")
    add_body(document, payload.get("histogram_summary"))
    add_subheading(document, "2.3 Resultados obtenidos y proximas acciones")
    add_body(document, payload.get("results_and_next_actions"))
    add_subheading(document, "2.4 Resultados obtenidos")
    add_body(document, payload.get("results_obtained"))
    add_subheading(document, "2.5 Proximas acciones")
    add_bullets(document, payload.get("next_actions"))
    add_subheading(document, "2.5.1 Requerimiento")
    add_bullets(document, payload.get("requirements"))

    add_heading(document, "3. Seguridad por Dominio")
    for domain in payload.get("security_domains") or []:
        add_subheading(document, domain.get("name") or "Dominio")
        add_body(document, domain.get("summary"))
        add_findings_table(document, domain.get("findings") or [])

    add_heading(document, "4. Reporte de acciones trabajadas durante la semana")
    add_bullets(document, payload.get("weekly_actions"))

    add_heading(document, "5. Resultados obtenidos")
    add_subheading(document, "5.1 Seguridad Reforzada")
    add_body(document, payload.get("reinforced_security"))
    add_subheading(document, "5.2 Hallazgos pendientes")
    add_bullets(document, payload.get("pending_findings"))

    add_heading(document, "6. Noticias de seguridad")
    for index, news in enumerate(payload.get("security_news") or [], start=1):
        add_subheading(document, news.get("title") or f"Noticia {index}")
        add_key_value_table(
            document,
            [
                ("Fecha", news.get("date")),
                ("Fuente", news.get("source")),
                ("Enlaces", ", ".join(news.get("links") or [])),
            ],
        )
        add_body(document, news.get("summary"))
        add_body(document, news.get("recommendation"))

    limitations = payload.get("limitations") or []
    if limitations:
        add_heading(document, "7. Limitaciones")
        add_bullets(document, limitations)


def validate_docx(path: Path) -> None:
    if not path.exists() or path.stat().st_size < 10_000:
        raise RuntimeError("DOCX was not generated correctly")
    with zipfile.ZipFile(path) as archive:
        if "word/document.xml" not in archive.namelist():
            raise RuntimeError("Generated DOCX does not contain word/document.xml")


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value.upper().strip())
    return value.strip("-")[:80] or "CLIENTE"


def build_output_filename(payload: dict[str, Any]) -> str:
    document_code = _clean_text(payload.get("document_code"))
    if document_code:
        return f"{_safe_name(document_code)}.docx"
    client = _safe_name(payload.get("client_name") or "CLIENTE")
    return f"MINORITY-REPORT-XOC_{client}_{date.today().isoformat()}.docx"


def generate_minority_report_docx(template_path: str, payload: dict[str, Any], output_path: str) -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    document = Document(template_path)
    apply_example_body_style(document)
    update_cover_and_footer(document, payload)
    clear_template_body_after_cover(document)
    build_report_body(document, payload)
    document.save(output_path)
    validate_docx(Path(output_path))
    return output_path
