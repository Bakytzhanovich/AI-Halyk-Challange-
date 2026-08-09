"""
computation_engine — считает actual-значение метрики по классифицированным
транзакциям. Чистый Python, детерминированно, без LLM.

Диспетчеризация по metric_name (из term_extractor) через ключевые слова —
term_extractor генерирует metric_name ЗАНОВО на каждый прогон (LLM
недетерминирован), одна и та же формула может называться по-разному
между прогонами. Ключевые слова там, где возможны коллизии между
разными формулами — сделаны специфичными; там, где в рамках этого
датасета концепт однозначен (например "insurance" — единственный
insurance-ковенант во всех 36 условиях) — используется одно слово
для устойчивости к переименованиям.
"""
from __future__ import annotations

import re

from schemas import ContractTerm, TransactionRecord
from kyc_linker import is_related_party


def _active(transactions: list[TransactionRecord]) -> list[TransactionRecord]:
    return [t for t in transactions if not t.excluded_by_audit]


def _split_by_currency(
    transactions: list[TransactionRecord], base_currency: str
) -> tuple[list[TransactionRecord], list[str]]:
    matching = [t for t in transactions if t.currency == base_currency]
    foreign = [t for t in transactions if t.currency != base_currency]
    warnings = [
        f"Транзакция {t.txn_id} в {t.currency} ({t.amount:,.2f}) исключена из "
        f"расчёта — базовая валюта {base_currency}, конвертация не выполняется "
        f"(нет фиксированного курса в договоре)"
        for t in foreign
    ]
    return matching, warnings


def _sum_category(transactions: list[TransactionRecord], category: str) -> float:
    return abs(sum(t.amount for t in transactions if t.category == category))


def _net_operating_expenses(active: list[TransactionRecord]) -> float:
    return sum(
        t.amount for t in active
        if t.category in ("opex", "payroll", "utilities", "insurance")
    )


def _calc_ebitda(active: list[TransactionRecord]) -> tuple[float, float]:
    revenue = sum(t.amount for t in active if t.category == "revenue")
    operating_costs = abs(_net_operating_expenses(active)) + _sum_category(active, "rent")
    return revenue - operating_costs, revenue


def calc_capital_intensity_ratio(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    capex = _sum_category(active, "capex")
    opex = abs(_net_operating_expenses(active))
    rent = _sum_category(active, "rent")
    denominator = opex + rent
    if denominator == 0:
        raise ValueError("OpEx + Rent = 0, деление на ноль")
    return capex / denominator, warnings


def calc_revenue(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    return sum(t.amount for t in active if t.category == "revenue"), warnings


def calc_related_party_payments(
    transactions: list[TransactionRecord],
    related_party_names: list[str],
    base_currency: str = "USD",
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    matched = [t for t in active if is_related_party(t.counterparty, related_party_names)]
    return abs(sum(t.amount for t in matched)), warnings


def calc_adjusted_ebitda_margin(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    ebitda, revenue = _calc_ebitda(active)
    if revenue == 0:
        raise ValueError("Revenue = 0, деление на ноль")
    warnings = warnings + [
        "adjusted_ebitda_margin: считается БЕЗ учёта разовых статей от "
        "аудитора (обратное добавление к EBITDA) — не хватает источника "
        "данных, результат приблизительный"
    ]
    return ebitda / revenue, warnings


def calc_interest_coverage_ratio(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    ebitda, _ = _calc_ebitda(active)
    interest = _sum_category(active, "interest")
    if interest == 0:
        raise ValueError("Interest expense = 0, деление на ноль")
    return ebitda / interest, warnings


def calc_individual_overhead_line_ceiling(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    payroll = _sum_category(active, "payroll")
    utilities = _sum_category(active, "utilities")
    return max(payroll, utilities), warnings


def calc_insurance_premium_to_expense_ratio(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    insurance = _sum_category(active, "insurance")
    denominator = _sum_category(active, "rent") + _sum_category(active, "utilities")
    if denominator == 0:
        raise ValueError("Rent + Utilities = 0, деление на ноль")
    return insurance / denominator, warnings


def calc_capital_expenditure(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    warnings = warnings + [
        "capital_expenditure: считается по факту транзакций категории capex, "
        "БЕЗ учёта переклассификаций аудитором в/из этой статьи"
    ]
    return _sum_category(active, "capex"), warnings


_MARKETING_DESCRIPTION_RE = re.compile(
    r"marketing|advertising|campaign|exhibition|media buy", re.I
)


def calc_marketing_expenses(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    """
    Сознательно НЕ использует общую категорию транзакций (в отличие от
    calc_capital_expenditure/_sum_category) — сканирует description
    напрямую. Причина: и публичный, и приватный датасет содержат
    "шумовые" транзакции с "ad campaign"/"exhibition ... marketing" у
    компаний БЕЗ ковенанта на маркетинг, лингвистически неотличимые от
    целевых. Если завести общую категорию "marketing" в
    transaction_analyzer, эти шумовые транзакции утекают в opex-базу
    ДРУГИХ компаний и меняют их EBITDA-зависимые метрики (сломало
    регрессию P1/6.1: 0.0448 -> 0.05). Эта функция вызывается только для
    компании, чья ИЗВЛЕЧЁННАЯ формула — про маркетинг (см. _DISPATCH),
    поэтому прямой скан description здесь безопасен и не задевает
    расчёты остальных компаний.
    """
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    matched = [t for t in active if _MARKETING_DESCRIPTION_RE.search(t.description)]
    warnings = warnings + [
        "marketing_expenses: считается по факту транзакций, текст которых "
        "матчит маркетинговые ключевые слова, БЕЗ учёта переклассификаций "
        "аудитором в/из этой статьи"
    ]
    return abs(sum(t.amount for t in matched)), warnings


def calc_debt_to_ebitda_ratio(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    debt = _sum_category(active, "financing")
    ebitda, _ = _calc_ebitda(active)
    if ebitda == 0:
        raise ValueError("EBITDA = 0, деление на ноль")
    return debt / ebitda, warnings


def calc_operating_expenses(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    warnings = warnings + [
        "operating_expenses: считается как сумма opex/payroll/utilities/"
        "insurance по факту транзакций, БЕЗ учёта переклассификаций "
        "аудитором в/из этих статей"
    ]
    return abs(_net_operating_expenses(active)), warnings


def calc_maximum_fiscal_burden_ratio(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    fiscal_burden = _sum_category(active, "tax") + _sum_category(active, "interest")
    revenue = sum(t.amount for t in active if t.category == "revenue")
    if revenue == 0:
        raise ValueError("Revenue = 0, деление на ноль")
    return fiscal_burden / revenue, warnings


def calc_minimum_cover_ratio(
    transactions: list[TransactionRecord], base_currency: str = "USD"
) -> tuple[float, list[str]]:
    active = _active(transactions)
    active, warnings = _split_by_currency(active, base_currency)
    revenue = sum(t.amount for t in active if t.category == "revenue")
    denominator = _sum_category(active, "capex") + abs(_net_operating_expenses(active))
    if denominator == 0:
        raise ValueError("OpEx + CapEx = 0, деление на ноль")
    warnings = warnings + [
        "minimum_cover_ratio: считается БЕЗ учёта 'поступлений по "
        "финансированию' — категории для них нет, результат может быть "
        "занижен относительно точного значения"
    ]
    return revenue / denominator, warnings


_KNOWN_UNSUPPORTED_KEYWORDS: list[tuple[list[tuple[str, ...]], str]] = [
    (
        [("personnel",), ("персонал", "пособ")],
        "формула требует данные о программе выходных пособий/сокращения "
        "персонала 'как раскрыто в примечаниях к отчётности' — источник "
        "не парсится текущим audit_adjuster",
    ),
    (
        [("tax", "utility"), ("налог", "казначейств")],
        "формула требует 'начисленные, но не уплаченные налоги, "
        "подтверждённые учётными данными казначейства' — такого источника "
        "данных нет ни в леджере, ни в документах",
    ),
    (
        [("capital", "expenditure", "ebitda"), ("консолидированн", "групп")],
        "формула считает капитальные затраты ГРУППЫ по консолидированной "
        "отчётности материнской компании — у нас есть только транзакции "
        "самого Заёмщика, консолидированных данных группы нет",
    ),
    (
        [("financing", "ebitda"), ("leverage",), ("поступлен", "финансир", "ebitda")],
        "формула требует 'поступления по финансированию' с конвертацией "
        "по курсу, раскрытому аудитором — ни категории, ни курса у нас нет",
    ),
    (
        [("asset", "transfer"), ("дочерн", "неограничен")],
        "формула требует список 'Неограниченных дочерних организаций' из "
        "KYC-досье и транзакции передачи активов дочерним структурам",
    ),
    (
        # F2 6.1 требует МАКСИМУМ по кварталам, а не сумму за весь период —
        # calc_marketing_expenses считает простую сумму, дал бы неверный
        # результат. Проверяется ДО диспетчера, чтобы не попасть в общий
        # ("marketing", "expense") дальше по функции.
        [("quarterly", "marketing"), ("маркетинг", "квартал")],
        "формула требует наибольшую КВАРТАЛЬНУЮ величину маркетинговых "
        "расходов, а не сумму за весь период — расчёт по кварталам не "
        "реализован (простая сумма дала бы неверный результат)",
    ),
]


_DISPATCH = {
    ("capital", "intensity"): calc_capital_intensity_ratio,
    ("capital", "expenditure"): calc_capital_expenditure,
    ("ebitda", "margin"): calc_adjusted_ebitda_margin,
    ("interest", "coverage"): calc_interest_coverage_ratio,
    ("overhead",): calc_individual_overhead_line_ceiling,
    ("insurance",): calc_insurance_premium_to_expense_ratio,
    ("cover", "sources"): calc_minimum_cover_ratio,
    ("revenue",): calc_revenue,
    ("marketing", "expense"): calc_marketing_expenses,
    ("debt", "ebitda"): calc_debt_to_ebitda_ratio,
    ("ebitda", "interest"): calc_interest_coverage_ratio,
    ("operating", "expense"): calc_operating_expenses,
    ("fiscal", "burden"): calc_maximum_fiscal_burden_ratio,
}


def compute_actual(
    term: ContractTerm,
    transactions: list[TransactionRecord],
    related_party_names: list[str] | None = None,
) -> tuple[float | None, list[str]]:
    search_text = (term.metric_name + " " + term.formula_description).lower()
    base_currency = term.currency or "USD"

    for keyword_groups, reason in _KNOWN_UNSUPPORTED_KEYWORDS:
        if any(all(kw in search_text for kw in group) for group in keyword_groups):
            return None, [f"{term.company_id} {term.clause_id}: НЕ СЧИТАЕТСЯ — {reason}"]

    if any(kw in search_text for kw in ("related", "связан", "affiliate", "аффилиров")):
        if related_party_names is None:
            raise ValueError(
                f"{term.company_id} {term.clause_id}: метрика похожа на "
                f"related-party, но related_party_names не переданы"
            )
        return calc_related_party_payments(transactions, related_party_names, base_currency)

    for keywords, func in _DISPATCH.items():
        if all(kw in search_text for kw in keywords):
            return func(transactions, base_currency)

    return None, []
