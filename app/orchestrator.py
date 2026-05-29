from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import base64
import re
from urllib.parse import urlparse

import requests

from app.context_manager import ResearchContext
from app.tools.files import write_markdown_report

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - explicit runtime error in _run_cua_for_url
    async_playwright = None
    PlaywrightTimeoutError = Exception


@dataclass
class CompetitiveIntelWorkflow:
    planner_scoring_uri: str
    planner_api_key: str
    planner_model: str
    browser_scoring_uri: str
    browser_api_key: str
    browser_model: str
    reporter_scoring_uri: str
    reporter_api_key: str
    reporter_model: str
    browser_cua_max_steps: int = 4
    browser_headless: bool = True
    browser_action_timeout_ms: int = 12000
    request_timeout_seconds: int = 180

    @staticmethod
    def _strip_think_blocks(text: str) -> str:
        return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()

    def _chat_completion(
        self,
        *,
        scoring_uri: str,
        api_key: str,
        model: str,
        messages: list[dict[str, object]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        content, _finish_reason = self._chat_completion_with_finish_reason(
            scoring_uri=scoring_uri,
            api_key=api_key,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return content

    def _chat_completion_with_finish_reason(
        self,
        *,
        scoring_uri: str,
        api_key: str,
        model: str,
        messages: list[dict[str, object]],
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        response = requests.post(
            scoring_uri,
            headers=headers,
            json=payload,
            timeout=self.request_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices", [])
        if not choices:
            raise RuntimeError(f"No choices in model response: {body}")
        first_choice = choices[0]
        message = first_choice.get("message", {})
        content = message.get("content", "")
        if not content:
            raise RuntimeError(f"No assistant content in model response: {body}")
        finish_reason = str(first_choice.get("finish_reason", "")).strip().lower()
        return self._strip_think_blocks(content), finish_reason

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        urls = re.findall(r"https?://[^\s)>\"]+", text)
        ordered_unique: list[str] = []
        for url in urls:
            cleaned = url.rstrip(".,;:")
            if cleaned not in ordered_unique:
                ordered_unique.append(cleaned)
        return ordered_unique

    @staticmethod
    def _slug_from_url(url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.replace(".", "-")
        path = parsed.path.strip("/").replace("/", "-")
        slug = f"{host}-{path}" if path else host
        return re.sub(r"[^A-Za-z0-9-]+", "-", slug).strip("-")[:80] or "page"

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, object]:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first == -1 or last == -1 or last <= first:
            return {}
        try:
            return json.loads(cleaned[first : last + 1])
        except json.JSONDecodeError:
            return {}

    def _generate_complete_report(self, *, user_query: str, notes_markdown: str) -> str:
        base_prompt = (
            "Create a complete markdown report from these notes.\n"
            "Required sections in this exact order:\n"
            "1) Pricing\n"
            "2) Product Launches & Innovations\n"
            "3) Customer Sentiment Signals\n"
            "4) Threats\n"
            "5) Opportunities\n"
            "6) Sources\n\n"
            "Requirements:\n"
            "- Cite concrete evidence from notes.\n"
            "- Include source URLs in the Sources section.\n"
            "- Do not include prefaces, analysis traces, or code fences.\n"
            "- End the response with a final line containing exactly: END_OF_REPORT\n\n"
            f"Original request:\n{user_query}\n\n"
            f"Notes:\n{notes_markdown}\n"
        )

        messages: list[dict[str, object]] = [{"role": "user", "content": base_prompt}]
        report_parts: list[str] = []
        for _ in range(4):
            chunk, finish_reason = self._chat_completion_with_finish_reason(
                scoring_uri=self.reporter_scoring_uri,
                api_key=self.reporter_api_key,
                model=self.reporter_model,
                messages=messages,
                max_tokens=1600,
                temperature=0.2,
            )
            report_parts.append(chunk)
            joined = "\n".join(part for part in report_parts if part.strip())
            if "END_OF_REPORT" in joined:
                return joined.split("END_OF_REPORT", 1)[0].rstrip()

            messages.extend(
                [
                    {"role": "assistant", "content": chunk},
                    {
                        "role": "user",
                        "content": (
                            "Continue exactly where you stopped. Do not repeat prior text. "
                            "Finish remaining sections and end with END_OF_REPORT."
                        ),
                    },
                ]
            )

            if finish_reason and finish_reason != "length":
                # Model stopped for non-length reason but still missed end marker.
                # One continuation attempt is still requested by the messages above.
                continue

        return "\n".join(part for part in report_parts if part.strip())

    async def _run_cua_for_url(self, *, url: str, objective: str, screenshot_root: Path) -> str:
        if async_playwright is None:
            raise RuntimeError(
                "playwright is required for CUA mode. Install dependencies and run: python -m playwright install chromium"
            )

        screenshot_root.mkdir(parents=True, exist_ok=True)
        url_slug = self._slug_from_url(url)
        trace_notes: list[str] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.browser_headless)
            context = await browser.new_context(viewport={"width": 1440, "height": 1024})
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            for step in range(1, self.browser_cua_max_steps + 1):
                screenshot_path = screenshot_root / f"{url_slug}-step{step}.png"
                screenshot_bytes = await page.screenshot(path=str(screenshot_path), full_page=False)
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
                prior_notes = "\n".join(trace_notes[-6:]) or "No prior notes."

                cua_prompt = (
                    f"You are operating as a browser computer-use agent for this objective:\n{objective}\n\n"
                    f"Current target URL: {url}\n"
                    f"Current page URL: {page.url}\n"
                    f"Step: {step}/{self.browser_cua_max_steps}\n"
                    f"Prior notes:\n{prior_notes}\n\n"
                    "From the screenshot, decide one next action and return JSON only with this schema:\n"
                    '{\n'
                    '  "action": "click_text|scroll_down|wait|finish",\n'
                    '  "value": "text to click or optional note",\n'
                    '  "note": "one factual observation from this screen",\n'
                    '  "findings": ["fact 1", "fact 2"]\n'
                    "}\n"
                    "Rules: choose finish once you have enough facts. Keep findings factual and concise."
                )

                action_text = self._chat_completion(
                    scoring_uri=self.browser_scoring_uri,
                    api_key=self.browser_api_key,
                    model=self.browser_model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": cua_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
                            ],
                        }
                    ],
                    max_tokens=400,
                    temperature=0.1,
                )

                action_payload = self._extract_json_object(action_text)
                action = str(action_payload.get("action", "finish")).strip().lower()
                value = str(action_payload.get("value", "")).strip()
                note = str(action_payload.get("note", "")).strip()
                findings = action_payload.get("findings", [])
                if note:
                    trace_notes.append(f"- step {step}: {note}")

                if action == "click_text":
                    if not value:
                        trace_notes.append(f"- step {step}: click_text skipped (missing value)")
                        continue
                    try:
                        locator = page.get_by_text(value, exact=False).first
                        await locator.click(timeout=self.browser_action_timeout_ms)
                        await page.wait_for_timeout(1200)
                    except PlaywrightTimeoutError:
                        trace_notes.append(f"- step {step}: click_text failed for '{value}'")
                elif action == "scroll_down":
                    await page.mouse.wheel(0, 900)
                    await page.wait_for_timeout(1000)
                elif action == "wait":
                    await page.wait_for_timeout(1500)
                elif action == "finish":
                    final_facts = [str(item).strip() for item in findings if str(item).strip()]
                    if final_facts:
                        trace_notes.extend([f"- step {step} fact: {fact}" for fact in final_facts])
                    break
                else:
                    trace_notes.append(f"- step {step}: unknown action '{action}', stopping.")
                    break

            visible_text = await page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 6000) : ''"
            )
            await context.close()
            await browser.close()

        synthesis_prompt = (
            "Create concise source-backed research notes for a competitive teardown.\n"
            f"Target URL: {url}\n"
            f"Objective: {objective}\n\n"
            f"CUA trace notes:\n{chr(10).join(trace_notes) if trace_notes else '- no trace notes'}\n\n"
            f"Visible page text excerpt:\n{visible_text}\n\n"
            "Return markdown bullet points only with concrete facts, then a short 'Source:' line with the URL."
        )
        synthesis = self._chat_completion(
            scoring_uri=self.browser_scoring_uri,
            api_key=self.browser_api_key,
            model=self.browser_model,
            messages=[{"role": "user", "content": synthesis_prompt}],
            max_tokens=550,
            temperature=0.1,
        )
        return synthesis

    async def run(self, user_query: str, output_path: Path) -> Path:
        context = ResearchContext()
        planner_prompt = f"""
You are a planning model for competitive intelligence.
Given this request, output a short plan and 2-3 high-quality public URLs to inspect.
Prefer official pricing pages, launch blogs, and product/company updates.

User request:
{user_query}

Output format:
PLAN:
- ...
URLS:
- https://...
- https://...
"""
        planner_text = self._chat_completion(
            scoring_uri=self.planner_scoring_uri,
            api_key=self.planner_api_key,
            model=self.planner_model,
            messages=[{"role": "user", "content": planner_prompt.strip()}],
            max_tokens=300,
            temperature=0.2,
        )

        urls = self._extract_urls(planner_text)
        if not urls:
            urls = [
                "https://openai.com/pricing",
                "https://www.anthropic.com/news",
                "https://www.microsoft.com/en-us/research/blog/",
            ]

        screenshot_root = output_path.parent / "screenshots" / output_path.stem
        for url in urls[:3]:
            findings = await self._run_cua_for_url(url=url, objective=user_query, screenshot_root=screenshot_root)
            context.add(url, findings)

        report_markdown = self._generate_complete_report(
            user_query=user_query,
            notes_markdown=context.as_markdown(),
        )
        final_path = write_markdown_report(str(output_path), report_markdown)
        return Path(final_path)
