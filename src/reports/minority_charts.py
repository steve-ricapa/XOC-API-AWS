from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


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


def _counts(source: dict[str, Any] | None) -> list[int]:
    data = source or {}
    return [int(data.get(SEVERITY_KEYS[severity], 0) or 0) for severity in SEVERITIES]


def _pie_chart(values: list[int], path: Path) -> None:
    safe_values = values if any(values) else [1, 0, 0, 0, 0]
    plt.figure(figsize=(3.4, 2.8), dpi=170)
    plt.pie(
        safe_values,
        labels=None,
        autopct=lambda pct: f"{pct:.1f}%" if pct > 0 and any(values) else "",
        startangle=90,
        colors=[PIE_COLORS[severity] for severity in SEVERITIES],
        pctdistance=0.68,
        textprops={"fontsize": 7.5},
        wedgeprops={"linewidth": 0.8, "edgecolor": "white"},
    )
    plt.tight_layout()
    plt.savefig(path, bbox_inches="tight")
    plt.close()


def _histogram(previous_values: list[int], current_values: list[int], path: Path, client_name: str) -> None:
    x = list(range(len(SEVERITIES)))
    width = 0.38
    plt.figure(figsize=(8.8, 4.2), dpi=170)
    previous_bars = plt.bar([value - width / 2 for value in x], previous_values, width, label="Semana anterior", color="#1F77B4")
    current_bars = plt.bar([value + width / 2 for value in x], current_values, width, label="Semana actual", color="#D62728")
    plt.title(f"Histograma de seguridad - {client_name}")
    plt.xticks(x, SEVERITIES)
    plt.ylabel("Cantidad de vulnerabilidades")
    plt.grid(axis="y", alpha=0.2)
    plt.legend(fontsize=8)
    for bars in (previous_bars, current_bars):
        for bar in bars:
            height = int(bar.get_height())
            plt.text(bar.get_x() + bar.get_width() / 2, height + 1, str(height), ha="center", fontsize=8)
    plt.tight_layout()
    plt.savefig(path, bbox_inches="tight")
    plt.close()


def generate_minority_charts(chart_data: dict[str, Any], output_dir: Path) -> list[dict[str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    client_name = str(chart_data.get("client_name") or "Cliente")
    previous_values = _counts(chart_data.get("previous_severity_summary"))
    current_values = _counts(chart_data.get("current_severity_summary"))

    previous_pie = output_dir / "figura_1_pastel_semana_anterior.png"
    current_pie = output_dir / "figura_2_pastel_semana_actual.png"
    histogram = output_dir / "figura_3_histograma_seguridad.png"

    _pie_chart(previous_values, previous_pie)
    _pie_chart(current_values, current_pie)
    _histogram(previous_values, current_values, histogram, client_name)

    return [
        {
            "path": str(previous_pie),
            "description": "Distribución de vulnerabilidades por severidad en la semana anterior.",
        },
        {
            "path": str(current_pie),
            "description": "Distribución de vulnerabilidades por severidad en la semana actual.",
        },
        {
            "path": str(histogram),
            "description": "Histograma de seguridad comparando semana anterior y semana actual.",
        },
    ]
