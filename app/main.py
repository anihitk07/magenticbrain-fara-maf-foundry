from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from app.orchestrator import CompetitiveIntelWorkflow


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Magentic/Fara competitive-intel multi-agent demo")
    parser.add_argument("--query", required=True, help="Competitive intelligence question to analyze")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to reports/report-<timestamp>.md",
    )
    return parser


async def run() -> None:
    load_dotenv()
    args = build_parser().parse_args()

    output_dir = Path(os.getenv("REPORT_OUTPUT_DIR", "reports"))
    default_name = f"report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    output_path = Path(args.output) if args.output else output_dir / default_name

    # Ensure reports/ and reports/screenshots/<run-id>/ exist on first run.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_dir = output_path.parent / "screenshots" / output_path.stem
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    workflow = CompetitiveIntelWorkflow(
        planner_scoring_uri=os.environ["PLANNER_SCORING_URI"],
        planner_api_key=os.environ["PLANNER_API_KEY"],
        planner_model=os.getenv("PLANNER_MODEL", "MagenticBrain-14B"),
        browser_scoring_uri=os.environ["BROWSER_SCORING_URI"],
        browser_api_key=os.environ["BROWSER_API_KEY"],
        browser_model=os.getenv("BROWSER_MODEL", "Fara1.5-9B"),
        reporter_scoring_uri=os.environ["REPORTER_SCORING_URI"],
        reporter_api_key=os.environ["REPORTER_API_KEY"],
        reporter_model=os.getenv("REPORTER_MODEL", "MagenticBrain-14B"),
        browser_cua_max_steps=int(os.getenv("BROWSER_CUA_MAX_STEPS", "4")),
        browser_headless=_get_bool("BROWSER_HEADLESS", True),
        browser_action_timeout_ms=int(os.getenv("BROWSER_ACTION_TIMEOUT_MS", "12000")),
    )
    final_path = await workflow.run(args.query, output_path)
    print(f"Report written to: {final_path}")


if __name__ == "__main__":
    asyncio.run(run())
