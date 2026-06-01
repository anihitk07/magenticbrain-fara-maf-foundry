from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

import requests

from app.context_manager import ResearchContext
from app.tools.files import write_markdown_report

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - explicit runtime error in CUA mode
    async_playwright = None
    PlaywrightTimeoutError = Exception


class BrowserStrategy:
    def __init__(self, workflow: CompetitiveIntelWorkflow, output_path: Path):
        self.workflow = workflow
        self.output_path = output_path

    async def run_for_url(self, *, url: str, objective: str) -> str:
        raise NotImplementedError


class FaraScreenshotLoopStrategy(BrowserStrategy):
    async def run_for_url(self, *, url: str, objective: str) -> str:
        screenshot_root = self.output_path.parent / "screenshots" / self.output_path.stem
        return await self.workflow._run_cua_for_url(
            url=url,
            objective=objective,
            screenshot_root=screenshot_root,
        )


class WebwrightStrategy(BrowserStrategy):
    async def run_for_url(self, *, url: str, objective: str) -> str:
        webwright_root = self.output_path.parent / "webwright" / self.output_path.stem
        return await asyncio.to_thread(
            self.workflow._run_webwright_for_url,
            url,
            objective,
            webwright_root,
            None,
        )


class WebwrightCachedScriptStrategy(BrowserStrategy):
    def _cache_key(self, *, url: str, objective: str) -> str:
        host = (urlparse(url).netloc or "site").replace(".", "-")
        objective_hash = hashlib.sha1(objective.encode("utf-8")).hexdigest()[:12]  # noqa: S324
        return f"{host}-{objective_hash}.py"

    async def run_for_url(self, *, url: str, objective: str) -> str:
        webwright_root = self.output_path.parent / "webwright" / self.output_path.stem
        cache_root = self.output_path.parent / "scripts"
        cache_root.mkdir(parents=True, exist_ok=True)
        cache_path = cache_root / self._cache_key(url=url, objective=objective)

        if cache_path.exists():
            try:
                return await asyncio.to_thread(
                    self.workflow._run_cached_script_for_url,
                    cache_path,
                    url,
                    objective,
                    webwright_root,
                )
            except Exception:
                # Fallback to a fresh Webwright run and refresh cache if script drifted.
                return await asyncio.to_thread(
                    self.workflow._run_webwright_for_url,
                    url,
                    objective,
                    webwright_root,
                    cache_path,
                )

        return await asyncio.to_thread(
            self.workflow._run_webwright_for_url,
            url,
            objective,
            webwright_root,
            cache_path,
        )


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
    browser_mode: str = "cua"
    browser_task_timeout_seconds: int = 900
    webwright_step_limit: int = 100
    webwright_require_self_reflection: bool = True
    webwright_sandbox_mode: str = "local"
    webwright_docker_image: str = ""
    browser_allowed_domains: tuple[str, ...] = ("openai.com", "anthropic.com", "microsoft.com")
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

    def _is_domain_allowed(self, url: str) -> bool:
        host = (urlparse(url).netloc or "").lower()
        if not host:
            return False
        for domain in self.browser_allowed_domains:
            normalized = domain.lower().strip()
            if not normalized:
                continue
            if host == normalized or host.endswith(f".{normalized}"):
                return True
        return False

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
                continue

        return "\n".join(part for part in report_parts if part.strip())

    def _make_browser_strategy(self, *, output_path: Path) -> BrowserStrategy:
        mode = (self.browser_mode or "cua").strip().lower()
        if mode == "webwright":
            return WebwrightStrategy(self, output_path)
        if mode == "webwright-craft":
            return WebwrightCachedScriptStrategy(self, output_path)
        return FaraScreenshotLoopStrategy(self, output_path)

    def _webwright_config_specs(self) -> list[str]:
        return [
            "base.yaml",
            "environment.browser_mode=local",
            "environment.shell=powershell",
            f"model.model_class=app.integrations.webwright_foundry_backend.WebwrightFoundryModel",
            f"model.model_name={self.browser_model}",
            f"model.foundry_endpoint={self.browser_scoring_uri}",
            f"model.foundry_api_key={self.browser_api_key}",
            f"agent.step_limit={self.webwright_step_limit}",
            f"agent.require_self_reflection_success={str(self.webwright_require_self_reflection).lower()}",
        ]

    @staticmethod
    def _extract_run_dir_from_webwright_output(output: str) -> Path | None:
        match = re.search(r"Running task in ([^\r\n]+)", output)
        if not match:
            return None
        candidate = Path(match.group(1).strip())
        return candidate if candidate.exists() else None

    def _latest_webwright_run_dir(self, *, root: Path, task_slug: str) -> Path | None:
        candidates = sorted(
            [path for path in root.glob(f"{task_slug}_*") if path.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _extract_webwright_final_response(self, trajectory_path: Path) -> str:
        if not trajectory_path.exists():
            return ""
        try:
            payload = json.loads(trajectory_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return ""
        if not isinstance(payload, list):
            return ""
        for item in reversed(payload):
            if not isinstance(item, dict):
                continue
            extra = item.get("extra", {})
            if not isinstance(extra, dict):
                continue
            for key in ("final_response", "submission", "run_exception"):
                value = extra.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _synthesize_notes_from_artifacts(
        self,
        *,
        url: str,
        objective: str,
        final_response: str,
        final_script_text: str,
        script_log_text: str,
        screenshot_paths: list[str],
        mode_label: str,
    ) -> str:
        prompt = (
            f"You are summarizing web-research evidence from {mode_label} artifacts.\n"
            f"Objective: {objective}\n"
            f"Target URL: {url}\n\n"
            f"Final response from run (if present):\n{final_response or 'none'}\n\n"
            f"Script excerpt:\n{final_script_text or 'none'}\n\n"
            f"Execution log excerpt:\n{script_log_text or 'none'}\n\n"
            f"Screenshots captured:\n{chr(10).join(screenshot_paths) if screenshot_paths else 'none'}\n\n"
            "Return markdown bullet points with concrete facts only, then add one final "
            "'Source: <url>' line."
        )
        return self._chat_completion(
            scoring_uri=self.browser_scoring_uri,
            api_key=self.browser_api_key,
            model=self.browser_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
            temperature=0.1,
        )

    def _run_webwright_for_url(
        self,
        url: str,
        objective: str,
        webwright_root: Path,
        cache_script_path: Path | None,
    ) -> str:
        if not self._is_domain_allowed(url):
            raise RuntimeError(
                f"Blocked URL outside allow-list: {url}. "
                "Set BROWSER_ALLOWED_DOMAINS to include this domain if intentional."
            )

        webwright_root.mkdir(parents=True, exist_ok=True)
        task_slug = f"{self._slug_from_url(url)}-{hashlib.sha1(objective.encode('utf-8')).hexdigest()[:8]}"  # noqa: S324
        before_dirs = {p.resolve() for p in webwright_root.glob(f"{task_slug}_*") if p.is_dir()}

        command: list[str] = [sys.executable, "-m", "webwright.run.cli"]
        for spec in self._webwright_config_specs():
            command.extend(["-c", spec])
        command.extend(
            [
                "-t",
                objective,
                "--start-url",
                url,
                "--task-id",
                task_slug,
                "-o",
                str(webwright_root),
            ]
        )
        if not self.browser_headless:
            command.append("--debug")

        project_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(project_root)
            if not existing_pythonpath
            else f"{project_root}{os.pathsep}{existing_pythonpath}"
        )

        sandbox_mode = (self.webwright_sandbox_mode or "local").strip().lower()
        if sandbox_mode == "docker":
            if not self.webwright_docker_image.strip():
                raise RuntimeError(
                    "WEBWRIGHT_SANDBOX_MODE=docker requires WEBWRIGHT_DOCKER_IMAGE to be set."
                )
            docker_command = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{project_root}:/workspace",
                "-w",
                "/workspace",
                "-e",
                "PYTHONPATH=/workspace",
                "-e",
                "OPENAI_API_KEY",
                "-e",
                "ANTHROPIC_API_KEY",
                "-e",
                "OPENROUTER_API_KEY",
                self.webwright_docker_image.strip(),
                "python",
                *command[1:],
            ]
            run = subprocess.run(
                docker_command,
                capture_output=True,
                text=True,
                timeout=self.browser_task_timeout_seconds,
                env=env,
                check=False,
            )
        else:
            run = subprocess.run(
                command,
                capture_output=True,
                text=True,
                cwd=str(project_root),
                timeout=self.browser_task_timeout_seconds,
                env=env,
                check=False,
            )
        output = (run.stdout or "") + "\n" + (run.stderr or "")
        run_dir = self._extract_run_dir_from_webwright_output(output)
        if run_dir is None:
            after_dirs = {p.resolve() for p in webwright_root.glob(f"{task_slug}_*") if p.is_dir()}
            created_dirs = sorted(
                [p for p in after_dirs - before_dirs if p.is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if created_dirs:
                run_dir = created_dirs[0]
            else:
                run_dir = self._latest_webwright_run_dir(root=webwright_root, task_slug=task_slug)

        if run.returncode != 0:
            tail = output[-4000:] if output else "(no command output)"
            raise RuntimeError(f"Webwright run failed for {url}.\n{tail}")
        if run_dir is None:
            raise RuntimeError(f"Webwright did not produce an output directory for {url}.")

        final_script_path = run_dir / "final_script.py"
        if cache_script_path is not None and final_script_path.exists():
            cache_script_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(final_script_path, cache_script_path)

        trajectory_path = run_dir / "trajectory.json"
        script_log_candidates = sorted(run_dir.glob("final_runs/*/final_script_log.txt"))
        script_log_path = script_log_candidates[-1] if script_log_candidates else None

        final_script_text = ""
        if final_script_path.exists():
            final_script_text = final_script_path.read_text(encoding="utf-8", errors="replace")[:12000]
        script_log_text = ""
        if script_log_path is not None and script_log_path.exists():
            script_log_text = script_log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        screenshots = sorted(path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*.png"))[:30]
        final_response = self._extract_webwright_final_response(trajectory_path)

        return self._synthesize_notes_from_artifacts(
            url=url,
            objective=objective,
            final_response=final_response,
            final_script_text=final_script_text,
            script_log_text=script_log_text,
            screenshot_paths=screenshots,
            mode_label="Webwright",
        )

    def _run_cached_script_for_url(
        self,
        script_path: Path,
        url: str,
        objective: str,
        webwright_root: Path,
    ) -> str:
        if not self._is_domain_allowed(url):
            raise RuntimeError(
                f"Blocked URL outside allow-list: {url}. "
                "Set BROWSER_ALLOWED_DOMAINS to include this domain if intentional."
            )

        webwright_root.mkdir(parents=True, exist_ok=True)
        run_slug = f"cached-{self._slug_from_url(url)}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        run_dir = webwright_root / run_slug
        run_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["WORKSPACE_DIR"] = str(run_dir)
        env["START_URL"] = url

        run = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            cwd=str(run_dir),
            timeout=self.browser_task_timeout_seconds,
            env=env,
            check=False,
        )
        output = (run.stdout or "") + "\n" + (run.stderr or "")
        if run.returncode != 0:
            tail = output[-4000:] if output else "(no command output)"
            raise RuntimeError(f"Cached script run failed for {url}.\n{tail}")

        screenshots = sorted(path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*.png"))[:30]
        return self._synthesize_notes_from_artifacts(
            url=url,
            objective=objective,
            final_response=(run.stdout or "")[-2000:],
            final_script_text=script_path.read_text(encoding="utf-8", errors="replace")[:12000],
            script_log_text=output[-8000:],
            screenshot_paths=screenshots,
            mode_label="cached Webwright script",
        )

    async def _run_cua_for_url(self, *, url: str, objective: str, screenshot_root: Path) -> str:
        if not self._is_domain_allowed(url):
            raise RuntimeError(
                f"Blocked URL outside allow-list: {url}. "
                "Set BROWSER_ALLOWED_DOMAINS to include this domain if intentional."
            )

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
        return self._chat_completion(
            scoring_uri=self.browser_scoring_uri,
            api_key=self.browser_api_key,
            model=self.browser_model,
            messages=[{"role": "user", "content": synthesis_prompt}],
            max_tokens=550,
            temperature=0.1,
        )

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

        browser_strategy = self._make_browser_strategy(output_path=output_path)
        for url in urls[:3]:
            findings = await browser_strategy.run_for_url(url=url, objective=user_query)
            context.add(url, findings)

        report_markdown = self._generate_complete_report(
            user_query=user_query,
            notes_markdown=context.as_markdown(),
        )
        final_path = write_markdown_report(str(output_path), report_markdown)
        return Path(final_path)
