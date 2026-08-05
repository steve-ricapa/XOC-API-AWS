from __future__ import annotations

from math import pi
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


SEVERITIES = ["Crítica", "Alta", "Media", "Baja", "Informativa"]
SEVERITY_KEYS = {
    "Crítica": "critical",
    "Alta": "high",
    "Media": "medium",
    "Baja": "low",
    "Informativa": "informational",
}
PIE_COLORS = {
    "Crítica": "#B00020",
    "Alta": "#E53935",
    "Media": "#FBC02D",
    "Baja": "#A5D6A7",
    "Informativa": "#43A047",
}
BAR_PREVIOUS = "#1F77B4"
BAR_CURRENT = "#D62728"
BACKGROUND = "#FFFFFF"
TEXT = "#1F3862"
GRID = "#D9E3EA"


def _counts(source: dict[str, Any] | None) -> list[int]:
    data = source or {}
    return [int(data.get(SEVERITY_KEYS[severity], 0) or 0) for severity in SEVERITIES]


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: ImageFont.ImageFont, fill: str) -> None:
    left, top, right, bottom = box
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = left + ((right - left - width) / 2)
    y = top + ((bottom - top - height) / 2)
    draw.text((x, y), text, font=font, fill=fill)


def _pie_chart(values: list[int], path: Path, title: str) -> None:
    image = Image.new("RGB", (1080, 760), BACKGROUND)
    draw = ImageDraw.Draw(image)
    title_font = _font(28, bold=True)
    label_font = _font(20)
    small_font = _font(18)

    draw.text((40, 24), title, fill=TEXT, font=title_font)

    chart_box = (70, 110, 610, 650)
    total = sum(values)
    safe_values = values if total > 0 else [1, 0, 0, 0, 0]
    total_safe = sum(safe_values)

    start = -pi / 2
    for severity, value in zip(SEVERITIES, safe_values):
        sweep = 2 * pi * (value / total_safe)
        draw.pieslice(chart_box, start=(start * 180 / pi), end=((start + sweep) * 180 / pi), fill=PIE_COLORS[severity], outline="#FFFFFF", width=2)
        start += sweep

    if total > 0:
        percentage_lines = []
        for severity, value in zip(SEVERITIES, values):
            if value <= 0:
                continue
            pct = (value / total) * 100
            percentage_lines.append(f"{severity}: {value} ({pct:.1f}%)")
    else:
        percentage_lines = ["Sin hallazgos para el periodo"]

    legend_x = 690
    legend_y = 130
    for index, severity in enumerate(SEVERITIES):
        y = legend_y + (index * 62)
        draw.rounded_rectangle((legend_x, y, legend_x + 24, y + 24), radius=5, fill=PIE_COLORS[severity])
        draw.text((legend_x + 38, y), severity, fill=TEXT, font=label_font)
        draw.text((legend_x + 38, y + 26), str(values[index]), fill="#5C6670", font=small_font)

    summary_y = 510
    for line in percentage_lines[:5]:
        draw.text((520, summary_y), line, fill=TEXT, font=small_font)
        summary_y += 28

    image.save(path, format="PNG")


def _histogram(previous_values: list[int], current_values: list[int], path: Path, client_name: str) -> None:
    image = Image.new("RGB", (1200, 680), BACKGROUND)
    draw = ImageDraw.Draw(image)
    title_font = _font(30, bold=True)
    axis_font = _font(18)
    value_font = _font(16)

    draw.text((40, 24), f"Histograma de seguridad - {client_name}", fill=TEXT, font=title_font)

    left = 90
    top = 110
    right = 1120
    bottom = 560

    max_value = max(previous_values + current_values + [1])
    chart_height = bottom - top
    chart_width = right - left
    groups = len(SEVERITIES)
    group_width = chart_width / groups
    bar_width = group_width * 0.24

    for step in range(6):
        y = bottom - (chart_height * step / 5)
        value = round(max_value * step / 5)
        draw.line((left, y, right, y), fill=GRID, width=1)
        draw.text((28, y - 10), str(value), fill="#5C6670", font=value_font)

    draw.line((left, top, left, bottom), fill=TEXT, width=2)
    draw.line((left, bottom, right, bottom), fill=TEXT, width=2)

    for index, severity in enumerate(SEVERITIES):
        group_center = left + (group_width * index) + (group_width / 2)
        prev_height = 0 if max_value == 0 else (previous_values[index] / max_value) * (chart_height - 10)
        curr_height = 0 if max_value == 0 else (current_values[index] / max_value) * (chart_height - 10)
        prev_left = group_center - bar_width - 6
        prev_right = group_center - 6
        curr_left = group_center + 6
        curr_right = group_center + bar_width + 6
        draw.rectangle((prev_left, bottom - prev_height, prev_right, bottom), fill=BAR_PREVIOUS)
        draw.rectangle((curr_left, bottom - curr_height, curr_right, bottom), fill=BAR_CURRENT)
        draw.text((prev_left, bottom - prev_height - 24), str(previous_values[index]), fill=BAR_PREVIOUS, font=value_font)
        draw.text((curr_left, bottom - curr_height - 24), str(current_values[index]), fill=BAR_CURRENT, font=value_font)
        bbox = draw.textbbox((0, 0), severity, font=axis_font)
        label_width = bbox[2] - bbox[0]
        draw.text((group_center - (label_width / 2), bottom + 16), severity, fill=TEXT, font=axis_font)

    legend_y = 610
    draw.rounded_rectangle((340, legend_y, 364, legend_y + 24), radius=4, fill=BAR_PREVIOUS)
    draw.text((374, legend_y), "Referencia anterior", fill=TEXT, font=axis_font)
    draw.rounded_rectangle((620, legend_y, 644, legend_y + 24), radius=4, fill=BAR_CURRENT)
    draw.text((654, legend_y), "Estado actual", fill=TEXT, font=axis_font)

    image.save(path, format="PNG")


def generate_minority_charts(chart_data: dict[str, Any], output_dir: Path) -> list[dict[str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    client_name = str(chart_data.get("client_name") or "Cliente")
    previous_values = _counts(chart_data.get("previous_severity_summary"))
    current_values = _counts(chart_data.get("current_severity_summary"))

    previous_pie = output_dir / "figura_1_pastel_semana_anterior.png"
    current_pie = output_dir / "figura_2_pastel_semana_actual.png"
    histogram = output_dir / "figura_3_histograma_seguridad.png"

    _pie_chart(previous_values, previous_pie, "Referencia anterior")
    _pie_chart(current_values, current_pie, "Estado actual")
    _histogram(previous_values, current_values, histogram, client_name)

    return [
        {
            "path": str(previous_pie),
            "description": "Distribución de vulnerabilidades por severidad en la referencia anterior disponible.",
        },
        {
            "path": str(current_pie),
            "description": "Distribución actual de vulnerabilidades por severidad.",
        },
        {
            "path": str(histogram),
            "description": "Comparación entre la referencia anterior disponible y el estado actual de la seguridad.",
        },
    ]
