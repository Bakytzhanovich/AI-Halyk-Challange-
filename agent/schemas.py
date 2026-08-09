"""
Общие Pydantic-схемы — контракт между модулями пайплайна.
Любой модуль, который читает или пишет данные другого модуля,
обязан использовать эти модели (не dict, не raw json).
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

ClauseId = Literal["6.1", "6.2", "6.3"]
Operator = Literal["<=", "<", ">=", ">", "=="]
DocType = Literal["contract", "audit", "kyc", "other"]


class ClassifiedDocument(BaseModel):
    """Результат document_classifier для одного файла."""

    file_path: str
    doc_type: DocType
    company_id: Optional[str] = None          # scenario_id, напр. "P1" — если удалось определить
    account_id: Optional[str] = None           # напр. "ACC-7801"
    is_draft: bool = False                      # найден маркер черновика/недействующей редакции
    covenant_period_start: Optional[date] = None
    covenant_period_end: Optional[date] = None
    is_authoritative: bool = False               # финальный вердикт contract_selector, не classifier
    raw_text_excerpt: str = Field(default="", max_length=500)  # первые ~500 символов для отладки
    confidence: float = 1.0                        # внутренняя, не идёт в submission.json


class ContractTerm(BaseModel):
    """Одно извлечённое условие ковенанта."""

    company_id: str
    clause_id: ClauseId
    metric_name: str                 # напр. "capital_intensity_ratio"
    operator: Operator
    threshold: float
    currency: Optional[str] = "USD"
    period_start: date
    period_end: date
    formula_description: str          # дословное/близкое к тексту описание формулы
    source_doc: str
    source_span: Optional[str] = None   # цитата/номер страницы для evidence


class TransactionRecord(BaseModel):
    txn_id: str
    company_id: Optional[str] = None
    account_id: str
    date: date
    counterparty: str
    description: str
    amount: float
    currency: str
    category: Optional[
        Literal[
            "capex", "opex", "rent", "revenue",
            "interest", "tax", "payroll", "utilities", "insurance",
            "related_party", "other",
        ]
    ] = None
    excluded_by_audit: bool = False
    audit_note: Optional[str] = None


class Evidence(BaseModel):
    doc_ids: list[str] = []
    txn_ids: list[str] = []
    notes: Optional[str] = None


class CovenantResult(BaseModel):
    company_id: str
    clause_id: ClauseId
    status: Literal["COMPLIANT", "BREACH"]
    actual: float
    evidence_txn_id: Optional[str] = None
    evidence: Evidence = Evidence()   # внутреннее поле, не идёт в submission.json


class Submission(BaseModel):
    team: str
    contact_email: str
    model: str
    answers: dict[str, dict[str, dict]]  # company_id -> clause_id -> {status, actual, evidence_txn_id}

    @classmethod
    def from_results(
        cls, team: str, contact_email: str, model: str, results: list[CovenantResult]
    ) -> "Submission":
        answers: dict[str, dict[str, dict]] = {}
        for r in results:
            answers.setdefault(r.company_id, {})[r.clause_id] = {
                "status": r.status,
                "actual": round(r.actual, 2),
                "evidence_txn_id": r.evidence_txn_id,
            }
        return cls(team=team, contact_email=contact_email, model=model, answers=answers)
