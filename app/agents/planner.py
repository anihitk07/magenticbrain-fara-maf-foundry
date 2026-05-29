from __future__ import annotations

from typing import Annotated

from agent_framework import Agent, tool

from app.context_manager import ResearchContext


def build_planner_agent(client: object, browser_agent: Agent, context: ResearchContext) -> Agent:
    @tool(approval_mode="never_require")
    async def delegate_browser_task(
        task: Annotated[str, "What to investigate on the target URL."],
        url: Annotated[str, "Target competitor URL for research."],
    ) -> str:
        prompt = (
            "Perform this browser research task and return concise factual notes.\n"
            f"Task: {task}\nURL: {url}\n"
            "Use fetch_page_text/fetch_page_headings tools as needed."
        )
        result = await browser_agent.run(prompt)
        notes = result.text.strip()
        context.add(f"{task} ({url})", notes)
        return notes

    return Agent(
        name="PlannerAgent",
        instructions=(
            "You are MagenticBrain-like planner/orchestrator for competitive intelligence.\n"
            "Break the user request into concrete sub-tasks, delegate web tasks through "
            "delegate_browser_task, and gather evidence-rich notes."
        ),
        client=client,
        tools=[delegate_browser_task],
    )

