"""
evaluator — сравнивает actual с threshold по operator из ContractTerm,
даёт финальный CovenantResult. Тривиальная логика, но именно она даёт
50% очков (status), поэтому не должно быть ни одной двусмысленности.
"""
from __future__ import annotations

from schemas import ContractTerm, CovenantResult, Evidence

_OPS = {
    "<=": lambda actual, threshold: actual <= threshold,
    "<": lambda actual, threshold: actual < threshold,
    ">=": lambda actual, threshold: actual >= threshold,
    ">": lambda actual, threshold: actual > threshold,
    "==": lambda actual, threshold: actual == threshold,
}


def evaluate(
    term: ContractTerm,
    actual: float,
    evidence: Evidence | None = None,
    evidence_txn_id: str | None = None,
) -> CovenantResult:
    op_func = _OPS[term.operator]
    is_compliant = op_func(actual, term.threshold)

    return CovenantResult(
        company_id=term.company_id,
        clause_id=term.clause_id,
        status="COMPLIANT" if is_compliant else "BREACH",
        actual=actual,
        evidence_txn_id=evidence_txn_id,
        evidence=evidence or Evidence(doc_ids=[term.source_doc]),
    )
