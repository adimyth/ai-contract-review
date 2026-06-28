import json
import os
import re
from collections.abc import Generator

import anthropic
import httpx
import openai as _openai
from loguru import logger
from models import ContractExtraction
from playbook import PLAYBOOK_SUMMARY

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]) if os.environ.get("ANTHROPIC_API_KEY") else None

ANTHROPIC_MODEL = "claude-sonnet-4-6"
OPENAI_MODEL = "gpt-5.4-mini"
MAX_TOKENS = 4096

# ── Tool schemas ──────────────────────────────────────────────────────────────
# Inner JSON Schema is shared; only the wrapper differs between providers.

_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_type": {
            "type": "string",
            "enum": ["NDA", "Order Form", "MSA", "SOW", "Other"],
            "description": "The type of contract.",
        },
        "parties": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Full legal names of all parties to the contract.",
        },
        "effective_date": {
            "type": "string",
            "description": "Effective date of the contract, or null if not stated.",
        },
        "clauses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Clause name (e.g. 'Confidentiality', 'Liability Cap')."},
                    "text": {"type": "string", "description": "Verbatim text of the clause or the key sentence(s)."},
                },
                "required": ["name", "text"],
            },
            "description": "All meaningful clauses extracted from the contract.",
        },
    },
    "required": ["contract_type", "parties", "clauses"],
}

# Verdict fields listed FIRST so the model generates them before clause_analysis,
# allowing early verdict SSE emission during streaming.
_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_level": {"type": "string", "enum": ["Low", "Medium", "High"]},
        "recommended_action": {
            "type": "string",
            "enum": ["Auto-approve", "Fast-track", "Full review", "Escalate"],
        },
        "executive_summary": {
            "type": "string",
            "description": "2-4 sentence plain-English summary of the contract and any issues, written for a non-lawyer.",
        },
        "clause_analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clause_name": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["Standard", "Minor deviation", "Non-standard", "Missing"],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["Low", "Medium", "High"],
                        "description": "Required when status is not 'Standard'.",
                    },
                    "issue": {
                        "type": "string",
                        "description": "Concise description of why this clause deviates from the playbook.",
                    },
                    "suggested_redline": {
                        "type": "string",
                        "description": "Specific replacement or addition language to bring the clause in line with the playbook.",
                    },
                },
                "required": ["clause_name", "status"],
            },
        },
        "auto_approved_clauses": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Clause names that match the playbook and need no human review.",
        },
    },
    "required": [
        "risk_level",
        "recommended_action",
        "executive_summary",
        "clause_analysis",
        "auto_approved_clauses",
    ],
}

# Anthropic format
_EXTRACTION_TOOL = {"name": "extract_contract", "description": "Extract structured information from a legal contract.", "input_schema": _EXTRACTION_SCHEMA}
_ANALYSIS_TOOL = {"name": "analyse_contract", "description": "Analyse extracted contract clauses against the company playbook and produce a review.", "input_schema": _ANALYSIS_SCHEMA}

# OpenAI format (same schema, different wrapper)
_OAI_EXTRACTION_TOOL = {"type": "function", "function": {"name": "extract_contract", "description": _EXTRACTION_TOOL["description"], "parameters": _EXTRACTION_SCHEMA}}
_OAI_ANALYSIS_TOOL = {"type": "function", "function": {"name": "analyse_contract", "description": _ANALYSIS_TOOL["description"], "parameters": _ANALYSIS_SCHEMA}}

_EXTRACTION_PROMPT = (
    "You are a legal document analyst. Extract the structured information from the contract below.\n\n"
    "<contract>\n{text}\n</contract>"
)

_ANALYSIS_PROMPT = (
    "You are a senior legal reviewer. "
    "Analyse the extracted contract clauses against the company's standard playbook positions.\n\n"
    "## Company Playbook\n\n"
    "{playbook}\n\n"
    "## Contract Details\n\n"
    "Type: {contract_type}\n"
    "Parties: {parties}\n"
    "Effective date: {effective_date}\n\n"
    "## Extracted Clauses\n\n"
    "{clauses}\n\n"
    "Guidance on recommended action:\n"
    "- Auto-approve: All clauses match the playbook. No human review needed.\n"
    "- Fast-track: Minor deviations only. A quick 10-minute human check is sufficient.\n"
    "- Full review: One or more meaningful deviations. Needs careful human review.\n"
    "- Escalate: High-severity issues (unlimited liability, missing DPA with personal data, extreme IP grab). Legal must review before responding."
)


# ── Shared streaming helpers ──────────────────────────────────────────────────

def _sse(event_type: str, payload: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


def _try_extract_verdict(json_str: str) -> dict | None:
    fields: dict[str, str] = {}
    for field in ("risk_level", "recommended_action", "executive_summary"):
        m = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"', json_str)
        if m:
            try:
                fields[field] = json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                fields[field] = m.group(1)
    return fields if len(fields) == 4 else None


def _parse_clauses_from_partial_json(json_str: str) -> list[dict]:
    m = re.search(r'"clause_analysis"\s*:\s*\[', json_str)
    if not m:
        return []
    rest = json_str[m.end():]
    clauses: list[dict] = []
    i = 0
    n = len(rest)
    while i < n:
        while i < n and rest[i] in " \t\n\r,":
            i += 1
        if i >= n or rest[i] == "]":
            break
        if rest[i] != "{":
            i += 1
            continue
        obj_start = i
        depth = 0
        in_string = False
        j = i
        while j < n:
            c = rest[j]
            if in_string:
                if c == "\\":
                    j += 2
                    continue
                if c == '"':
                    in_string = False
            else:
                if c == '"':
                    in_string = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            clause = json.loads(rest[obj_start: j + 1])
                            clauses.append(clause)
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        break
            j += 1
        else:
            break
    return clauses


def _emit_streaming_events(accumulated: str, emitted_clauses: int, verdict_emitted: bool):
    """Shared logic to emit verdict + clause SSE events from partial JSON. Returns updated counts."""
    events = []
    if not verdict_emitted:
        verdict = _try_extract_verdict(accumulated)
        if verdict:
            logger.info(f"Step 2 verdict — risk={verdict.get('risk_level')} action={verdict.get('recommended_action')}")
            events.append(_sse("verdict", verdict))
            verdict_emitted = True
    all_clauses = _parse_clauses_from_partial_json(accumulated)
    while emitted_clauses < len(all_clauses):
        clause = all_clauses[emitted_clauses]
        logger.info(f"Step 2 clause {emitted_clauses + 1} — {clause.get('clause_name')} ({clause.get('status')})")
        events.append(_sse("clause", clause))
        emitted_clauses += 1
    return events, emitted_clauses, verdict_emitted


# ── Anthropic pipeline ────────────────────────────────────────────────────────

def _extract_anthropic(contract_text: str, client: anthropic.Anthropic) -> ContractExtraction:
    logger.info(f"Step 1 start [Anthropic] — {len(contract_text)} chars")
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        tools=[_EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "extract_contract"},
        messages=[{"role": "user", "content": _EXTRACTION_PROMPT.format(text=contract_text)}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    result = ContractExtraction.model_validate(tool_use.input)
    logger.info(f"Step 1 done [Anthropic] — type={result.contract_type} clauses={len(result.clauses)}")
    return result


def _stream_analyse_anthropic(extraction: ContractExtraction, client: anthropic.Anthropic) -> Generator[str, None, None]:
    logger.info(f"Step 2 start [Anthropic] — {len(extraction.clauses)} clauses")
    clauses_text = "\n\n".join(f"**{c.name}**\n{c.text}" for c in extraction.clauses)
    prompt = _ANALYSIS_PROMPT.format(
        playbook=PLAYBOOK_SUMMARY,
        contract_type=extraction.contract_type,
        parties=", ".join(extraction.parties),
        effective_date=extraction.effective_date or "Not stated",
        clauses=clauses_text,
    )
    accumulated = ""
    emitted_clauses = 0
    verdict_emitted = False
    final_data: dict = {}

    with client.messages.stream(
        model=ANTHROPIC_MODEL,
        max_tokens=MAX_TOKENS,
        tools=[_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "analyse_contract"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "input_json_delta":
                accumulated += event.delta.partial_json
                events, emitted_clauses, verdict_emitted = _emit_streaming_events(accumulated, emitted_clauses, verdict_emitted)
                yield from events

        final_msg = stream.get_final_message()
        tool_use = next((b for b in final_msg.content if b.type == "tool_use"), None)
        if tool_use:
            final_data = tool_use.input

    for clause in final_data.get("clause_analysis", [])[emitted_clauses:]:
        yield _sse("clause", clause)
    if not verdict_emitted and final_data:
        yield _sse("verdict", {k: final_data.get(k) for k in ("risk_level", "recommended_action", "executive_summary")})

    approved = final_data.get("auto_approved_clauses", [])
    logger.info(f"Step 2 done [Anthropic] — {emitted_clauses} clauses, {len(approved)} auto-approved")
    yield _sse("approved", {"auto_approved_clauses": approved})
    yield _sse("done", {})


# ── OpenAI pipeline ───────────────────────────────────────────────────────────

def _extract_openai(contract_text: str, client: _openai.OpenAI) -> ContractExtraction:
    logger.info(f"Step 1 start [OpenAI] — {len(contract_text)} chars")
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        tools=[_OAI_EXTRACTION_TOOL],
        tool_choice={"type": "function", "function": {"name": "extract_contract"}},
        messages=[{"role": "user", "content": _EXTRACTION_PROMPT.format(text=contract_text)}],
    )
    tool_call = response.choices[0].message.tool_calls[0]
    result = ContractExtraction.model_validate(json.loads(tool_call.function.arguments))
    logger.info(f"Step 1 done [OpenAI] — type={result.contract_type} clauses={len(result.clauses)}")
    return result


def _stream_analyse_openai(extraction: ContractExtraction, client: _openai.OpenAI) -> Generator[str, None, None]:
    logger.info(f"Step 2 start [OpenAI] — {len(extraction.clauses)} clauses")
    clauses_text = "\n\n".join(f"**{c.name}**\n{c.text}" for c in extraction.clauses)
    prompt = _ANALYSIS_PROMPT.format(
        playbook=PLAYBOOK_SUMMARY,
        contract_type=extraction.contract_type,
        parties=", ".join(extraction.parties),
        effective_date=extraction.effective_date or "Not stated",
        clauses=clauses_text,
    )
    accumulated = ""
    emitted_clauses = 0
    verdict_emitted = False

    stream = client.chat.completions.create(
        model=OPENAI_MODEL,
        tools=[_OAI_ANALYSIS_TOOL],
        tool_choice={"type": "function", "function": {"name": "analyse_contract"}},
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.tool_calls:
            delta = chunk.choices[0].delta.tool_calls[0].function.arguments
            if delta:
                accumulated += delta
                events, emitted_clauses, verdict_emitted = _emit_streaming_events(accumulated, emitted_clauses, verdict_emitted)
                yield from events

    try:
        final_data = json.loads(accumulated)
    except json.JSONDecodeError:
        final_data = {}

    for clause in final_data.get("clause_analysis", [])[emitted_clauses:]:
        yield _sse("clause", clause)
    if not verdict_emitted and final_data:
        yield _sse("verdict", {k: final_data.get(k) for k in ("risk_level", "recommended_action", "executive_summary")})

    approved = final_data.get("auto_approved_clauses", [])
    logger.info(f"Step 2 done [OpenAI] — {emitted_clauses} clauses, {len(approved)} auto-approved")
    yield _sse("approved", {"auto_approved_clauses": approved})
    yield _sse("done", {})


# ── Public entry point ────────────────────────────────────────────────────────

def review_contract_stream(contract_text: str, filename: str, api_key: str | None = None) -> Generator[str, None, None]:
    use_openai = bool(api_key and not api_key.startswith("sk-ant-"))
    provider = "OpenAI" if use_openai else "Anthropic"
    logger.info(f"Review start [{provider}] — file={filename!r} chars={len(contract_text)}")

    if use_openai:
        client = _openai.OpenAI(api_key=api_key, http_client=httpx.Client())
        extraction = _extract_openai(contract_text, client)
    else:
        client = anthropic.Anthropic(api_key=api_key) if api_key else _client
        if client is None:
            raise ValueError("No API key provided. Please enter your Anthropic or OpenAI key.")
        extraction = _extract_anthropic(contract_text, client)

    yield _sse("meta", {
        "contract_type": extraction.contract_type,
        "parties": extraction.parties,
        "effective_date": extraction.effective_date,
    })

    if use_openai:
        yield from _stream_analyse_openai(extraction, client)
    else:
        yield from _stream_analyse_anthropic(extraction, client)
