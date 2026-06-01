# MagenticBrain + Fara1.5 — Multi‑Agent Competitive Intelligence on Azure AI Foundry

A reference implementation that wires Microsoft Research's brand‑new small agentic models — **MagenticBrain‑14B** (orchestrator) and **Fara1.5‑9B** (browser computer‑use agent) — into a multi‑agent **Competitive Intelligence Analyst** running on **Azure AI Foundry hub‑based projects**.

> 🧠 MagenticBrain plans, codes, and delegates.
> 🖥️ Fara1.5 can run in two CUA paths: direct screenshot-action loop or Webwright terminal-driven browser automation.
> 📑 A reporter agent compiles a complete competitive teardown into Markdown.

---

## Why this repo

These two models were released as part of Microsoft Research's [MagenticLite, MagenticBrain, Fara1.5 announcement](https://www.microsoft.com/en-us/research/blog/magenticlite-magenticbrain-fara1-5-an-agentic-experience-optimized-for-small-models/). They are designed to be **codesigned** — small models + a tight harness — and they currently ship via the **hub‑based** Foundry catalog (`ai.azure.com/catalog`), not the new Foundry resource (project‑centric) catalog.

This repo shows how to:

1. **Deploy** both models as managed online endpoints inside a hub‑based Foundry project.
2. **Use Fara1.5 as a real Computer‑Use Agent** (CUA) — Playwright captures live browser screenshots, Fara picks the next action, the harness executes it.
3. **Use MagenticBrain‑14B as orchestrator and reporter** — planning the work and assembling the final Markdown report (with continuation‑based generation so reports never get truncated mid‑section).

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  Python host (this repo)                                            │
│                                                                     │
│   1. Planner agent ─► MagenticBrain‑14B                            │
│        proposes plan + target URLs                                  │
│                                                                     │
│   2. Browser agent  ─► Fara1.5‑9B                                   │
│        mode=cua: screenshot ─► action ─► execute                    │
│        mode=webwright: terminal code loop via Webwright             │
│        mode=webwright-craft: cache + reuse generated scripts        │
│                                                                     │
│   3. Reporter agent ─► MagenticBrain‑14B                           │
│        continuation‑based generation, ends on END_OF_REPORT        │
│                                                                     │
│   → reports/report-<ts>.md                                          │
│   → reports/screenshots/report-<ts>/*.png                           │
└────────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

- An **Azure AI Foundry hub** and a **hub‑based project** in a subscription/region with GPU quota for the chosen SKU.
- **GPU quota** for one of:
  - `Standard_NC24ads_A100_v4` (A100 80GB) — recommended for the 14B + 9B pair, 48 vCPU total.
  - `Standard_NC40ads_H100_v5` (H100 80GB) — best perf if you have the quota.
- Azure CLI with the ML extension: `az extension add -n ml`.
- Python 3.11+ and an `az login` session that can reach the project workspace.

Suggested footprint (pick names and a region that fit your quota):

| Field | Example |
| --- | --- |
| Subscription | `<your-subscription>` |
| Region | `<your-region>` |
| Resource group | `<your-rg>` |
| Hub workspace | `<your-hub>` |
| Project workspace | `<your-project>` |
| Endpoint names | `magenticbrain-14b`, `fara15-9b` |
| SKU per endpoint | `Standard_NC24ads_A100_v4` (1 × A100 80GB) |

---

## 1) Deploy the two models on Foundry (hub‑based project)

The models live in the `azureml-cua-ai-frontiers-p` registry.

```bash
# Create endpoints
az ml online-endpoint create -f deploy/magenticbrain-endpoint.yml \
  -g <RG> -w <PROJECT_WORKSPACE> --subscription <SUB_ID>

az ml online-endpoint create -f deploy/fara-endpoint.yml \
  -g <RG> -w <PROJECT_WORKSPACE> --subscription <SUB_ID>

# Deploy models behind the endpoints
az ml online-deployment create -f deploy/magenticbrain-deployment.yml \
  --all-traffic -g <RG> -w <PROJECT_WORKSPACE> --subscription <SUB_ID>

az ml online-deployment create -f deploy/fara-deployment.yml \
  --all-traffic -g <RG> -w <PROJECT_WORKSPACE> --subscription <SUB_ID>
```

Or run the convenience script:

```powershell
.\deploy\deploy-models.ps1 `
  -SubscriptionId <SUB_ID> `
  -ResourceGroup <RG> `
  -WorkspaceName <PROJECT_WORKSPACE>
```

The deployment YAMLs reference:

- `azureml://registries/azureml-cua-ai-frontiers-p/models/MagenticBrain-14B/versions/1`
- `azureml://registries/azureml-cua-ai-frontiers-p/models/Fara1.5-9B/versions/1`

When you're done, tear them down to stop GPU charges:

```powershell
.\deploy\teardown-models.ps1 `
  -SubscriptionId <SUB_ID> `
  -ResourceGroup <RG> `
  -WorkspaceName <PROJECT_WORKSPACE>
```

---

## 2) Configure the app

Install dependencies (including Playwright and pinned Webwright):

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Copy `.env.example` to `.env` and fill in the endpoint URIs and keys:

```env
PLANNER_SCORING_URI=https://<your-magenticbrain-endpoint>.<region>.inference.ml.azure.com/v1/chat/completions
PLANNER_API_KEY=<endpoint-key>
PLANNER_MODEL=MagenticBrain-14B

BROWSER_SCORING_URI=https://<your-fara-endpoint>.<region>.inference.ml.azure.com/v1/chat/completions
BROWSER_API_KEY=<endpoint-key>
BROWSER_MODEL=Fara1.5-9B
BROWSER_MODE=cua
BROWSER_CUA_MAX_STEPS=4
BROWSER_HEADLESS=true
BROWSER_ACTION_TIMEOUT_MS=12000
BROWSER_TASK_TIMEOUT_SECONDS=900
WEBWRIGHT_STEP_LIMIT=100
WEBWRIGHT_REQUIRE_SELF_REFLECTION=true
WEBWRIGHT_SANDBOX_MODE=local
WEBWRIGHT_DOCKER_IMAGE=
BROWSER_ALLOWED_DOMAINS=openai.com,anthropic.com,microsoft.com

REPORTER_SCORING_URI=https://<your-magenticbrain-endpoint>.<region>.inference.ml.azure.com/v1/chat/completions
REPORTER_API_KEY=<endpoint-key>
REPORTER_MODEL=MagenticBrain-14B

REPORT_OUTPUT_DIR=reports
```

> Resolve the endpoint URI and key from your own deployment with:
> `az ml online-endpoint show -n <endpoint-name> -g <rg> -w <project> --query scoring_uri -o tsv`
> `az ml online-endpoint get-credentials -n <endpoint-name> -g <rg> -w <project> --query primaryKey -o tsv`

To watch the browser UI live, set `BROWSER_HEADLESS=false`.

### Browser modes

- `BROWSER_MODE=cua` (default): direct screenshot→action loop in `app/orchestrator.py`.
- `BROWSER_MODE=webwright`: use the Webwright runner (`python -m webwright.run.cli`) with a Foundry-managed-endpoint model backend.
- `BROWSER_MODE=webwright-craft`: same as `webwright`, plus cache the generated `final_script.py` and reuse it on later runs.

Safety controls:

- `BROWSER_ALLOWED_DOMAINS` enforces a domain allow-list before any CUA step.
- `WEBWRIGHT_SANDBOX_MODE=docker` runs Webwright inside a container when `WEBWRIGHT_DOCKER_IMAGE` is supplied.

---

## 3) Run the multi‑agent app

```powershell
python -m app.main --query "Create a competitive teardown of OpenAI vs Anthropic pricing, launches, and customer sentiment compared to Microsoft."
```

Output:

- Markdown report: `reports\report-<timestamp>.md`
- Screenshot evidence per URL/step: `reports\screenshots\report-<timestamp>\*.png`

Sample sections in the generated report:

1. Pricing
2. Product Launches & Innovations
3. Customer Sentiment Signals
4. Threats
5. Opportunities
6. Sources

---

## Project layout

```
magentic-maf-demo/
├── app/
│   ├── main.py                 # CLI entry point
│   ├── orchestrator.py         # Planner → Fara CUA loop → Reporter
│   ├── context_manager.py      # Accumulates per‑URL research notes
│   ├── agents/                 # Reusable agent builders
│   └── tools/                  # File/report writers
├── deploy/
│   ├── magenticbrain-endpoint.yml
│   ├── magenticbrain-deployment.yml
│   ├── fara-endpoint.yml
│   ├── fara-deployment.yml
│   ├── deploy-models.ps1
│   └── teardown-models.ps1
├── reports/                    # Generated reports + screenshots
├── requirements.txt
├── .env.example
└── run-competitive-intel.md
```

---

## Design notes

- **Why managed online endpoints?** MagenticBrain‑14B and Fara1.5‑9B are listed in the Foundry **hub‑based** catalog. To consume them programmatically you deploy them as managed online endpoints in a project workspace.
- **Why a custom harness?** The official harness (MagenticLite) is opinionated; this repo deliberately uses a small Python harness so the deployment + CUA wiring is easy to read end‑to‑end.
- **Why continuation‑based reporting?** Long, multi‑section reports can exceed `max_tokens` in a single response. The reporter loops with `finish_reason=length` and an `END_OF_REPORT` sentinel to guarantee complete output.
- **Why Playwright?** Fara1.5 is a vision/CUA model — it expects to reason over screenshots. Playwright captures screenshots, executes actions Fara returns (`click_text`, `scroll_down`, `wait`, `finish`), and produces an auditable per‑step image trail.
- **Why Webwright support?** For longer or more complex browsing tasks, Webwright's terminal/code action space can be more robust than single primitive actions. This repo exposes it behind `BROWSER_MODE=webwright`.

---

## Cost guardrails

A100/H100 endpoints are expensive when idle. Use `deploy/teardown-models.ps1` between demos, or scale the deployment instance count to 0.

---

## References

- 📰 [MagenticLite, MagenticBrain, Fara1.5 — Microsoft Research blog](https://www.microsoft.com/en-us/research/blog/magenticlite-magenticbrain-fara1-5-an-agentic-experience-optimized-for-small-models/)
- 🧠 [Fara1.5 Computer Use Agent](https://www.microsoft.com/en-us/research/articles/fara1-5-computer-use-agent/)
- 📚 [Azure AI Foundry — managed online endpoints](https://learn.microsoft.com/azure/machine-learning/how-to-deploy-online-endpoints)

---

## License

MIT.
