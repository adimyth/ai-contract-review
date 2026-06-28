import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from loguru import logger

# Route all stdlib logging (uvicorn, fastapi, httpx, …) through loguru
class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
# Silence httpx request-level chatter; errors still pass through
logging.getLogger("httpx").setLevel(logging.WARNING)

logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}:{line}</cyan> — <level>{message}</level>",
    colorize=True,
    level="INFO",
)

import anthropic
import openai as _openai
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from typing import Annotated
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent))

from parser import extract_text
from pipeline import review_contract_stream
from playbook import STANDARD_POSITIONS

app = FastAPI(title="Contract Review AI", version="1.0.0")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
CONTRACTS_DIR = Path(__file__).parent.parent / "mock_contracts"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# Metadata for each bundled sample contract
SAMPLES = [
    {
        "filename": "commonpaper_mutual_nda_v1.pdf",
        "label": "Common Paper Mutual NDA v1.0",
        "type": "NDA",
        "source": "Common Paper",
        "description": "Industry-standard NDA drafted by 40+ attorneys. Published under CC BY 4.0.",
    },
    {
        "filename": "bonterms_mutual_nda_v1.pdf",
        "label": "Bonterms Mutual NDA v1.0",
        "type": "NDA",
        "source": "Bonterms",
        "description": "Open-source NDA template used by thousands of companies. CC BY 4.0.",
    },
    {
        "filename": "bonterms_cloud_terms_v1.pdf",
        "label": "Bonterms Cloud Terms v1.0",
        "type": "SaaS / Cloud",
        "source": "Bonterms",
        "description": "Balanced enterprise SaaS subscription terms. 7-page master agreement.",
    },
    {
        "filename": "bonterms_psa_v1.2.pdf",
        "label": "Bonterms PSA v1.2",
        "type": "Professional Services",
        "source": "Bonterms",
        "description": "Professional services agreement covering deliverables, IP, warranties, and escalation.",
    },
]


def _extract_error_msg(e: Exception) -> str:
    # OpenAI SDK exposes .message directly; Anthropic buries it in .body
    if msg := getattr(e, "message", None):
        return str(msg)
    body = getattr(e, "body", None) or {}
    if isinstance(body, dict):
        error = body.get("error") or {}
        if isinstance(error, dict) and (msg := error.get("message")):
            return str(msg)
    # Last resort: strip class prefix from str(e) if it looks like "ErrorClass: ..."
    return str(e).split(": ", 1)[-1] if ": " in str(e) else str(e)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/samples")
def list_samples():
    return SAMPLES


@app.get("/api/samples/{filename}")
def get_sample(filename: str):
    safe_name = Path(filename).name  # strip any path traversal
    path = CONTRACTS_DIR / safe_name
    if not path.exists() or not any(s["filename"] == safe_name for s in SAMPLES):
        raise HTTPException(status_code=404, detail="Sample not found.")
    return FileResponse(str(path))


@app.get("/api/playbook")
def get_playbook():
    return [
        {"clause": clause, "standard_position": position}
        for clause, position in STANDARD_POSITIONS.items()
    ]


@app.post("/api/review")
async def review(
    file: UploadFile = File(...),
    x_api_key: Annotated[str | None, Header()] = None,
):
    if file.content_type not in (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".pdf", ".docx", ".doc"):
            raise HTTPException(
                status_code=415,
                detail="Unsupported file type. Please upload a PDF or DOCX file.",
            )

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10 MB.")

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="File is empty.")

    try:
        text = extract_text(file.filename or "upload.pdf", content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if len(text.strip()) < 100:
        raise HTTPException(
            status_code=422,
            detail="Document appears to contain very little text. Please ensure it is not a scanned image.",
        )

    filename = file.filename or "upload"

    def generate():
        try:
            yield from review_contract_stream(text, filename, api_key=x_api_key or None)
        except Exception as e:
            msg = _extract_error_msg(e)
            logger.error("Review failed for {!r}: {}", filename, msg)
            yield f"data: {json.dumps({'type': 'error', 'detail': msg})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Serve frontend — must come after API routes
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
