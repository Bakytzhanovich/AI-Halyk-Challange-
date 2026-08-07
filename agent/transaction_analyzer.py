"""
transaction_analyzer — классифицирует транзакции по description на категории,
нужные для расчёта ковенантов. Чистый Python, без LLM — детерминированно
и воспроизводимо, как договаривались (код считает, LLM только понимает документы).

ВАЖНО (из ручного разбора P1): проценты по займам и налоги НЕ входят в OpEx —
это financing costs / below-the-line статьи по МСФО, на которые прямо
ссылается договор ("данные финансовой отчётности по МСФО"). Они уходят
в категорию "financing_or_tax" и не участвуют в расчёте 6.1.

Related-party НЕ определяется здесь по ключевым словам — это отдельный
шаг (kyc_linker), т.к. зависит от списка связанных сторон конкретной
компании из её KYC-досье, а не от текста description.
"""
from __future__ import annotations

import re
from pathlib import Path

from schemas import TransactionRecord

Category = str  # "capex" | "opex" | "rent" | "revenue" | "financing_or_tax" | "other"

# Порядок проверки важен: более специфичные категории — раньше.
# Каждая запись: (regex по description, категория)
CLASSIFICATION_RULES: list[tuple[re.Pattern, Category]] = [
    # --- Financing / tax — исключаются из OpEx по МСФО (below-the-line) ---
    (re.compile(r"\binterest\b|\bcoupon\b|revolver", re.I), "financing_or_tax"),
    (re.compile(r"\btax\b|excise|franchise tax", re.I), "financing_or_tax"),
    (re.compile(r"default interest|penalty settlement", re.I), "financing_or_tax"),

    # --- CapEx — приобретение основных средств / оборудования ---
    (re.compile(r"purchase of .*(equipment|crane|machinery|vehicle)", re.I), "capex"),

    # --- Rent — аренда/лизинг земли, помещений ---
    (re.compile(r"\blease\b.*payment|land lease|rent for\b", re.I), "rent"),

    # --- Revenue — доход от основной деятельности ---
    (re.compile(r"sales settlement|service(s)? revenue|stevedoring", re.I), "revenue"),

    # --- Opex — операционные расходы (широкая категория, много совпадений) ---
    (
        re.compile(
            r"payroll|insurance|electricity|utility|marketing|maintenance|"
            r"telecom|water charge|waste|servicing|inspection|compensation",
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


def load_transactions(ledger_path: Path, account_id: str) -> list[TransactionRecord]:
    import csv
    from datetime import date as date_cls

    records = []
    with open(ledger_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["account_id"] != account_id:
                continue
            records.append(
                TransactionRecord(
                    txn_id=row["txn_id"],
                    account_id=row["account_id"],
                    date=date_cls.fromisoformat(row["date"]),
                    counterparty=row["counterparty"],
                    description=row["description"],
                    amount=float(row["amount"]),
                    currency=row["currency"],
                    category=classify_category(row["description"]),
                )
            )
    return records


if __name__ == "__main__":
    import sys
    from collections import defaultdict

    ledger_path = Path(sys.argv[1])
    account_id = sys.argv[2]

    txns = load_transactions(ledger_path, account_id)
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
