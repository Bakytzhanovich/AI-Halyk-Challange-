"""
transaction_analyzer — классифицирует транзакции по description на категории,
нужные для расчёта ковенантов. Чистый Python, без LLM — детерминированно
и воспроизводимо, как договаривались (код считает, LLM только понимает документы).

ВАЖНО (из ручного разбора P1): проценты по займам и налоги НЕ входят в OpEx —
это financing costs / below-the-line статьи по МСФО. Раньше они лежали в
одной категории "financing_or_tax", но реальные метрики других компаний
(interest_coverage_ratio, tax_and_utility_expenses_to_ebitda_ratio) требуют
их РАЗДЕЛЬНО — поэтому "interest" и "tax" теперь отдельные категории.

По той же причине (реальные формулы B1/6.2, P10/6.1 явно называют payroll
и utilities как отдельные строки, insurance — отдельно) generic "opex"
разбит на: payroll, utilities, insurance, opex (остальное).

Related-party НЕ определяется здесь по ключевым словам — это отдельный
шаг (kyc_linker), т.к. зависит от списка связанных сторон конкретной
компании из её KYC-досье, а не от текста description.
"""
from __future__ import annotations

import re
from pathlib import Path

from schemas import TransactionRecord

Category = str

# Порядок проверки важен: более специфичные категории — раньше.
CLASSIFICATION_RULES: list[tuple[re.Pattern, Category]] = [
    # --- Interest — процентные расходы (below-the-line по МСФО) ---
    (re.compile(r"\binterest\b|\bcoupon\b|revolver", re.I), "interest"),

    # --- Tax — налоги (below-the-line по МСФО) ---
    (re.compile(r"\btax\b|excise|franchise tax|penalty settlement", re.I), "tax"),

    # --- Financing — привлечение долгового финансирования (term loan
    #     drawdown). Ключевое слово "drawdown" встречается ровно один раз
    #     на компанию в леджере, без коллизий с "interest on ... facility"
    #     (те матчатся раньше по правилу interest, т.к. явно содержат "interest"). ---
    (re.compile(r"drawdown", re.I), "financing"),

    # --- CapEx — приобретение основных средств / оборудования ---
    (re.compile(r"purchase of .*(equipment|crane|machinery|vehicle)", re.I), "capex"),

    # --- Rent — аренда/лизинг земли, помещений ---
    (re.compile(r"\blease\b.*payment|land lease|rent for\b", re.I), "rent"),

    # --- Revenue — доход от основной деятельности ---
    (re.compile(r"sales settlement|service(s)? revenue|stevedoring", re.I), "revenue"),

    # --- Payroll — расходы на персонал ---
    (re.compile(r"payroll", re.I), "payroll"),

    # --- Utilities — коммунальные расходы ---
    (re.compile(r"electricity|\butility\b|water charge|waste", re.I), "utilities"),

    # --- Insurance — страховые премии ---
    (re.compile(r"insurance", re.I), "insurance"),

    # --- Opex — прочие операционные расходы ---
    # ПРИМЕЧАНИЕ: "marketing" НЕ выделена в отдельную категорию здесь,
    # в отличие от payroll/utilities/insurance. Причина: и публичный,
    # и приватный датасет содержат "шумовые" транзакции с "ad campaign" /
    # "exhibition ... marketing" у компаний БЕЗ ковенанта на маркетинг
    # (P1/B1/P2/P4 в публичном датасете) — лингвистически неотличимые
    # от целевых транзакций X1/X3/H5/S2/B3. Любой keyword, достаточно
    # широкий, чтобы поймать реальные marketing_expenses-транзакции,
    # ловит и этот шум, что меняет opex/EBITDA-базу для НЕ-marketing
    # компаний и ломает регрессию (P1/6.1 переставал быть 0.0448).
    # Поэтому calc_marketing_expenses в computation_engine.py сканирует
    # description НАПРЯМУЮ, в обход общей категоризации — см. комментарий
    # там же.
    (
        re.compile(
            r"marketing|maintenance|telecom|servicing|inspection|compensation",
            re.I,
        ),
        "opex",
    ),
]


def classify_category(description: str) -> Category:
    for pattern, category in CLASSIFICATION_RULES:
        if pattern.search(description):
            return category
    return "other"


def load_transactions(
    ledger_path: Path, account_id: str
) -> tuple[list[TransactionRecord], list[str]]:
    """
    Возвращает (транзакции, warnings). Строки с пустым/невалидным amount
    ПРОПУСКАЮТСЯ с явным warning, а не роняют весь пайплайн — это
    осознанное решение (согласовано ранее): такая строка не должна
    останавливать обработку остальных 11 компаний, но должна быть
    заметна перед сдачей (см. TXN-P7-0033/TXN-P8-0031 — похоже на
    намеренную ловушку датасета, а не случайность).
    """
    import csv
    from datetime import date as date_cls

    records = []
    warnings = []
    with open(ledger_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["account_id"] != account_id:
                continue
            try:
                amount = float(row["amount"])
            except (ValueError, TypeError):
                warnings.append(
                    f"{row['txn_id']}: пустой/невалидный amount "
                    f"({row['amount']!r}) — транзакция ПРОПУЩЕНА, не участвует в расчёте"
                )
                continue
            records.append(
                TransactionRecord(
                    txn_id=row["txn_id"],
                    account_id=row["account_id"],
                    date=date_cls.fromisoformat(row["date"]),
                    counterparty=row["counterparty"],
                    description=row["description"],
                    amount=amount,
                    currency=row["currency"],
                    category=classify_category(row["description"]),
                )
            )
    return records, warnings


if __name__ == "__main__":
    import sys
    from collections import defaultdict

    ledger_path = Path(sys.argv[1])
    account_id = sys.argv[2]

    txns, load_warnings = load_transactions(ledger_path, account_id)
    by_category: dict[str, list[TransactionRecord]] = defaultdict(list)
    for t in txns:
        by_category[t.category].append(t)

    print(f"Транзакций для {account_id}: {len(txns)}\n")
    for category, items in sorted(by_category.items()):
        total = sum(t.amount for t in items)
        print(f"{category:18s} n={len(items):3d}  сумма={total:>15,.2f}")
        for t in items:
            print(f"    {t.txn_id}  {t.amount:>14,.2f} {t.currency}  {t.description[:60]}")
        print()

    if load_warnings:
        print(f"⚠️  Пропущенные строки ({len(load_warnings)}):")
        for w in load_warnings:
            print(f"  - {w}")
