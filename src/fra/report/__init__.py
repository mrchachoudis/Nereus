"""Report rendering: FinalReport -> markdown + JSON sidecar + figures."""

from __future__ import annotations

from fra.report.figures import build_all_figures
from fra.report.render import build_final_report, render_markdown, write_report

__all__ = ["build_all_figures", "build_final_report", "render_markdown", "write_report"]
