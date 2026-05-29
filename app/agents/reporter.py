from __future__ import annotations

from agent_framework import Agent


def build_reporter_agent(client: object) -> Agent:
    return Agent(
        name="ReporterAgent",
        instructions=(
            "Write executive-ready competitive teardown reports in markdown with sections: "
            "Pricing, Recent Launches, Sentiment Signals, Threats, Opportunities, and Sources."
        ),
        client=client,
    )

