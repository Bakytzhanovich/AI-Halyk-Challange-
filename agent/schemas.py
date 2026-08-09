"""
Общие Pydantic-схемы — контракт между модулями пайплайна.
Любой модуль, который читает или пишет данные другого модуля,
обязан использовать эти модели (не dict, не raw json).
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

ClauseId = str  # было Literal["6.1","6.2","6.3"] — датасет содержит
                # и другие пункты (5.1-5.3 у J4, 6.4 у X1/X2/X3)
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
            "financing",
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
        cls,
        team: str,
        contact_email: str,
        model: str,
        results: list[CovenantResult],
        company_ids: Optional[list[str]] = None,
        company_clause_ids: Optional[dict[str, list[str]]] = None,
    ) -> "Submission":
        """
        company_ids — полный список company_id, ожидаемых в submission
        (напр. все значения account_to_scenario), не только те, для которых
        есть результат. Нужен, чтобы структура JSON СТРОГО соответствовала
        submission_template.json: каждая компания присутствует как ключ
        всегда, с null-заглушками там, где значение не посчитано.

        company_clause_ids — company_id -> список ожидаемых clause_id
        (обычно из submission_template.json). Датасет больше не гарантирует
        ровно 6.1/6.2/6.3 на компанию (J4: 5.1-5.3, X1-X3: 6.1-6.4) —
        без явного маппинга для конкретной компании используется старый
        список ("6.1","6.2","6.3") как fallback для обратной совместимости.
        """
        answers: dict[str, dict[str, dict]] = {}
        for r in results:
            answers.setdefault(r.company_id, {})[r.clause_id] = {
                "status": r.status,
                "actual": round(r.actual, 2),
                "evidence_txn_id": r.evidence_txn_id,
            }

        all_company_ids = set(company_ids or []) | set(answers.keys())
        for company_id in all_company_ids:
            clauses = answers.setdefault(company_id, {})
            expected_clauses = (company_clause_ids or {}).get(
                company_id, ("6.1", "6.2", "6.3")
            )
            for clause_id in expected_clauses:
                clauses.setdefault(
                    clause_id,
                    {"status": None, "actual": None, "evidence_txn_id": None},
                )

        return cls(team=team, contact_email=contact_email, model=model, answers=answers)
