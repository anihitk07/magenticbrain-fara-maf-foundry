from __future__ import annotations

from pathlib import Path
from typing import Annotated

from agent_framework import tool


@tool(approval_mode="never_require")
def write_markdown_report(
    output_path: Annotated[str, "Path where the report should be written."],
    content: Annotated[str, "Markdown content to save."],
) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path.resolve())

