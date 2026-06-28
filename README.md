# Contract Review AI

AI-powered contract pre-screening tool. Upload a PDF or DOCX contract (or pick from the bundled samples), and the AI analyses it against a playbook of standard legal positions — flagging deviations, suggesting redlines, scoring risk, and recommending an action in seconds.

Built with FastAPI + Claude / OpenAI + vanilla JS. Stateless — no files are stored after each request.

## How it works

The review runs as a two-step AI pipeline:

**Step 1 — Extraction**

The uploaded document is parsed to plain text, then sent to the AI with a forced tool call (`extract_contract`). The model returns structured JSON: contract type, parties, effective date, and every meaningful clause with its verbatim text.

**Step 2 — Analysis (streaming)**

The extracted clauses are sent to the AI alongside the full playbook. A second forced tool call (`analyse_contract`) produces:

- **Risk level** — Low / Medium / High
- **Recommended action** — Auto-approve / Fast-track / Full review / Escalate
- **Executive summary** — plain-English overview for a non-lawyer
- **Clause-by-clause analysis** — status (Standard / Minor deviation / Non-standard / Missing), severity, issue description, and a suggested redline for every non-standard clause
- **Auto-approved clauses** — clauses that match the playbook and need no human attention

Results stream to the browser as Server-Sent Events so clause cards appear one-by-one while the analysis is still running.

## API keys and sessions

There is no backend authentication. API keys are handled entirely client-side:

- On first visit the app prompts for an API key (Anthropic or OpenAI).
- The key is stored in **`sessionStorage`** — it lives only for the current browser tab and is wiped when the tab is closed. It is never persisted anywhere on the server.
- Each request sends the key in an `X-Api-Key` header. The backend uses it to construct a per-request AI client and discards it immediately after the response.
- If no key is provided, the backend falls back to its own server-side key (if one was configured at startup).

**Provider detection** is automatic: keys starting with `sk-ant-` route to Anthropic (Claude); any other key routes to OpenAI.

## Running locally

The frontend is served by FastAPI as static files — there is no separate frontend build step.

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the server
uvicorn backend.main:app --reload --port 8000
```

Open http://localhost:8000. Enter your Anthropic or OpenAI API key when prompted, then upload a contract or click **"Try a sample"**.

To configure a server-side fallback key so the app works without the user supplying one:

```bash
ANTHROPIC_API_KEY=sk-ant-... uvicorn backend.main:app --reload --port 8000
```

## Bundled sample contracts

All samples are public, open-source contracts (CC BY 4.0):

| File | Description |
|------|-------------|
| `commonpaper_mutual_nda_v1.pdf` | Common Paper Mutual NDA v1.0 |
| `bonterms_mutual_nda_v1.pdf` | Bonterms Mutual NDA v1.0 |
| `bonterms_cloud_terms_v1.pdf` | Bonterms Cloud Terms v1.0 — enterprise SaaS agreement |
| `bonterms_psa_v1.2.pdf` | Bonterms Professional Services Agreement v1.2 |

## Customising the playbook

Standard legal positions live in `backend/playbook.py`. Edit the `STANDARD_POSITIONS` dict there — no other code changes needed. The playbook is read verbatim by the AI prompt and also exposed via `GET /api/playbook` for the in-app modal.

## API surface

```
POST /api/review          multipart/form-data (PDF or DOCX, max 10 MB) -> SSE stream
GET  /api/samples         list bundled sample contracts
GET  /api/samples/{name}  serve a bundled contract file
GET  /api/playbook        return playbook rules as JSON
GET  /                    serve the frontend
```

## Deployment (Render)

1. Push to GitHub
2. Create a **Web Service** on Render pointing at the repo — it picks up `render.yaml` automatically
3. Optionally set a server-side API key in Render's Environment settings as a fallback
