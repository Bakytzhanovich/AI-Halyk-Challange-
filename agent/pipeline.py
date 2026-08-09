"""
pipeline — склеивает все модули в один прогон:
documents/ + master_ledger_2025.csv  ->  submission.json

Требует ANTHROPIC_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY для term_extractor.
Остальные шаги — чистый Python.

Запуск:
    python3 agent/pipeline.py <documents_dir> <ledger_path> <team> <email> <output_path>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from audit_adjuster import apply_adjustments, extract_cutoff_adjustments
from computation_engine import compute_actual
from contract_selector import select_contracts
from document_classifier import classify_all
from evaluator import evaluate
from kyc_linker import extract_related_parties
from scenario_mapper import build_mapping
from schemas import CovenantResult, Submission
from term_extractor import MODEL, extract_all
from transaction_analyzer import load_transactions


def _company_docs_text(all_docs, account_id: str, doc_type: str) -> str:
    """Склеивает текст всех документов заданного типа для данного account_id."""
    import fitz

    parts = []
    for doc in all_docs:
        if doc.doc_type == doc_type and doc.account_id == account_id:
            with fitz.open(doc.file_path) as f:
                parts.append("\n".join(page.get_text() for page in f))
    return "\n\n".join(parts)


def run_pipeline(
    documents_dir: Path,
    ledger_path: Path,
    team: str,
    contact_email: str,
    output_path: Path,
    submission_template_path: Path | None = None,
) -> None:
    all_warnings: list[str] = []

    expected_scenario_ids: set[str] | None = None
    if submission_template_path is not None:
        template = json.loads(submission_template_path.read_text(encoding="utf-8"))
        expected_scenario_ids = set(template["answers"].keys())

    print("1/6 document_classifier...")
    all_docs = classify_all(documents_dir)

    print("2/6 contract_selector...")
    selected_docs, sel_warnings = select_contracts(all_docs)
    all_warnings += sel_warnings

    print("3/6 scenario_mapper...")
    account_to_scenario, map_warnings = build_mapping(ledger_path, expected_scenario_ids)
    all_warnings += map_warnings

    print("4/6 term_extractor (использует Claude API)...")
    selected_paths = {
        account_id: Path(doc.file_path) for account_id, doc in selected_docs.items()
    }
    terms, extract_warnings = extract_all(selected_paths, account_to_scenario)
    all_warnings += extract_warnings

    terms_by_company: dict[str, list] = {}
    for t in terms:
        terms_by_company.setdefault(t.company_id, []).append(t)

    print("5/6 transaction_analyzer + kyc_linker + audit_adjuster + computation_engine...")
    results: list[CovenantResult] = []

    for account_id, company_id in account_to_scenario.items():
        company_terms = terms_by_company.get(company_id, [])
        if not company_terms:
            all_warnings.append(f"{company_id}: нет извлечённых условий, пропущен")
            continue

        transactions, load_warnings = load_transactions(ledger_path, account_id)
        if load_warnings:
            all_warnings.extend(f"{company_id}: {w}" for w in load_warnings)

        kyc_text = _company_docs_text(all_docs, account_id, "kyc")
        related_names = [name for name, _ in extract_related_parties(kyc_text)]

        audit_text = _company_docs_text(all_docs, account_id, "audit")
        adjustments = extract_cutoff_adjustments(audit_text)

        # Ковенантный период берём из первого условия компании — у всех
        # пунктов одной компании он должен совпадать (проверка ниже).
        period_start = company_terms[0].period_start
        period_end = company_terms[0].period_end
        transactions = apply_adjustments(transactions, adjustments, period_start, period_end)

        for term in company_terms:
            if term.period_start != period_start or term.period_end != period_end:
                all_warnings.append(
                    f"{company_id} {term.clause_id}: период отличается от "
                    f"остальных пунктов компании — ПРОВЕРЬ ВРУЧНУЮ"
                )
            actual, currency_warnings = compute_actual(
                term, transactions, related_party_names=related_names
            )
            if currency_warnings:
                all_warnings.extend(
                    f"{company_id} {term.clause_id}: {w}" for w in currency_warnings
                )
            if actual is None:
                all_warnings.append(
                    f"{company_id} {term.clause_id}: метрика '{term.metric_name}' "
                    f"не распознана computation_engine — нужен ручной разбор, ПРОПУЩЕНО"
                )
                continue
            results.append(evaluate(term, actual))

    print("6/6 сборка submission.json...")
    submission = Submission.from_results(
        team=team,
        contact_email=contact_email,
        model=MODEL,
        results=results,
        company_ids=list(account_to_scenario.values()),
    )
    output_path.write_text(submission.model_dump_json(indent=2), encoding="utf-8")

    print(f"\n✅ Готово: {len(results)} условий записано в {output_path}")
    if all_warnings:
        unique_warnings = list(dict.fromkeys(all_warnings))
        print(f"\n⚠️  Предупреждения ({len(unique_warnings)}) — ПРОВЕРЬ ПЕРЕД СДАЧЕЙ:")
        for w in unique_warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    if len(sys.argv) not in (6, 7):
        print(
            "Использование: python3 pipeline.py "
            "<documents_dir> <ledger_path> <team> <email> <output_path> "
            "[submission_template_path]"
        )
        sys.exit(1)

    run_pipeline(
        documents_dir=Path(sys.argv[1]),
        ledger_path=Path(sys.argv[2]),
        team=sys.argv[3],
        contact_email=sys.argv[4],
        output_path=Path(sys.argv[5]),
        submission_template_path=Path(sys.argv[6]) if len(sys.argv) == 7 else None,
    )
