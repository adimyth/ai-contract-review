# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run dev server (from project root) — frontend is served by FastAPI, no separate step
ANTHROPIC_API_KEY=<key> uvicorn backend.main:app --reload --port 8000
```

No test suite yet. Manual verification: start the server and upload each file in `contracts/`.

## Architecture

**Single FastAPI service** (`backend/main.py`) that serves both the REST API and the frontend static files. No separate frontend build step — the frontend is plain HTML/JS with Tailwind CDN.

**Request flow:**

```
POST /api/review (multipart file upload)
  → parser.py      — extract text from PDF (PyMuPDF) or DOCX (python-docx)
  → pipeline.py    — two Claude API calls (tool_choice=forced):
      Step 1: extract_contract tool → ContractExtraction (structured clauses)
      Step 2: analyse_contract tool → risk level, redlines, recommended action
  → ContractReview Pydantic model → JSON response
```

**Key files:**
- `backend/playbook.py` — PortSwigger's standard legal positions. Update this to change what the AI checks against. No other code changes needed.
- `backend/pipeline.py` — Both Claude tool schemas live here alongside the two API calls. Claude `claude-sonnet-4-6`, forced tool use via `tool_choice={"type": "tool", "name": "..."}`.
- `backend/models.py` — Pydantic v2 models shared between pipeline output and API response.
- `backend/main.py` — Also exposes `/api/samples`, `/api/samples/{filename}`, and `/api/playbook` endpoints. The `SAMPLES` list in this file controls which contracts appear in the UI.
- `frontend/app.js` — All UI state (upload tab, sample tab, processing, results) and result rendering. No framework.
- `mock_contracts/` — 4 mock DOCX contracts + 4 real-world PDFs (Bonterms, Common Paper). All CC BY 4.0.

## Deployment

Render web service. Config in `render.yaml`. Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 2`. Set `ANTHROPIC_API_KEY` as an environment variable in Render dashboard.

## Key constraints

- **Stateless** — no files written to disk, no database, no sessions.
- File size limit: 10 MB enforced in `main.py`.
- Accepted formats: PDF and DOCX only.
- The frontend is served as static files from `frontend/` by FastAPI's `StaticFiles` mount — it must remain the last mount in `main.py` so API routes take precedence.
