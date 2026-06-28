from pydantic import BaseModel
from typing import Literal


class ExtractedClause(BaseModel):
    name: str
    text: str


class ContractExtraction(BaseModel):
    contract_type: Literal["NDA", "Order Form", "MSA", "SOW", "Other"]
    parties: list[str]
    effective_date: str | None
    clauses: list[ExtractedClause]


class ClauseAnalysis(BaseModel):
    clause_name: str
    status: Literal["Standard", "Minor deviation", "Non-standard", "Missing"]
    severity: Literal["Low", "Medium", "High"] | None
    issue: str | None
    suggested_redline: str | None


class ContractReview(BaseModel):
    contract_type: str
    parties: list[str]
    effective_date: str | None
    risk_level: Literal["Low", "Medium", "High"]
    recommended_action: Literal["Auto-approve", "Fast-track", "Full review", "Escalate"]
    executive_summary: str
    clause_analysis: list[ClauseAnalysis]
    auto_approved_clauses: list[str]
