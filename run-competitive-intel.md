# Competitive Intelligence Demo Runbook

## Prereqs
- Deployed model endpoints in the selected Foundry project:
  - `magenticbrain-14b`
  - `fara15-9b`
- `.env` created from `.env.example` with:
  - `*_SCORING_URI` values set to each endpoint `scoring_uri` (already includes `/v1/chat/completions`)
  - `*_API_KEY` values from `az ml online-endpoint get-credentials`
- `az login` completed for the target tenant/subscription

## Install
```powershell
cd C:\Flutter\magentic-maf-demo
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Run
```powershell
cd C:\Flutter\magentic-maf-demo
python -m app.main --query "Create a competitive teardown of Vendor X pricing, launches, and customer sentiment compared to us."
```

## CUA behavior
- `BROWSER_MODE=cua` runs Fara in a screenshot-driven loop per URL.
- `BROWSER_MODE=webwright` runs Webwright's terminal-driven browser loop using the Foundry endpoint as model backend.
- `BROWSER_MODE=webwright-craft` adds script caching/reuse for repeatable tasks.
- For `cua`, the loop captures browser screenshots, asks Fara for next action (`click_text`, `scroll_down`, `wait`, `finish`), executes the action in Playwright, then synthesizes findings.
- Screenshots are saved under `reports\screenshots\report-<timestamp>\`.

## Expected output sections
1. Pricing
2. Recent Launches
3. Sentiment Signals
4. Threats
5. Opportunities
6. Sources
