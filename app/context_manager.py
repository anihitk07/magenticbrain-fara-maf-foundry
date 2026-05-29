from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResearchContext:
    notes: list[str] = field(default_factory=list)

    def add(self, title: str, content: str) -> None:
        self.notes.append(f"## {title}\n{content.strip()}\n")

    def as_markdown(self) -> str:
        return "\n".join(self.notes)

