from __future__ import annotations

from typing import Annotated

import requests
from agent_framework import Agent, tool
from bs4 import BeautifulSoup


def build_browser_agent(client: object) -> Agent:
    @tool(approval_mode="never_require")
    def fetch_page_text(
        url: Annotated[str, "Target URL to fetch for competitor research."],
    ) -> str:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:12000]

    @tool(approval_mode="never_require")
    def fetch_page_headings(
        url: Annotated[str, "Target URL to extract headings from."],
    ) -> str:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        headings: list[str] = []
        for tag in soup.find_all(["h1", "h2", "h3"]):
            value = " ".join(tag.get_text(separator=" ").split())
            if value:
                headings.append(value)
        return "\n".join(headings[:80])

    @tool(approval_mode="never_require")
    def request_critical_approval(
        reason: Annotated[str, "Why this step needs human confirmation."],
    ) -> str:
        answer = input(f"[HITL] {reason}\nType 'yes' to continue: ").strip().lower()
        return "approved" if answer == "yes" else "denied"

    return Agent(
        name="BrowserAgent",
        instructions=(
            "You are the web research specialist. Use tools to gather competitor information. "
            "Before actions that could be sensitive/irreversible, call request_critical_approval."
        ),
        client=client,
        tools=[fetch_page_text, fetch_page_headings, request_critical_approval],
    )

