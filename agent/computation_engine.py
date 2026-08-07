"""
computation_engine — считает actual-значение метрики по классифицированным
транзакциям. Чистый Python, детерминированно, без LLM.

Диспетчеризация по metric_name (из term_extractor) через ключевые слова —
не жёсткий whitelist, потому что у других 11 компаний могут быть другие
метрики/формулировки, которых мы ещё не видели. Если метрика не распознана —
функция явно возвращает None и просит ручной разбор, а не молча врёт нулём.
"""
from __future__ import annotations

from schemas import ContractTerm, TransactionRecord
from kyc_linker import is_related_party


def _active(transactions: list[TransactionRecord]) -> list[TransactionRecord]:
    """Транзакции, не исключённые audit_adjuster."""
    return [t for t in transactions if not t.excluded_by_audit]


def calc_capital_intensity_ratio(transactions: list[TransactionRecord]) -> float:
    active = _active(transactions)
    # ВАЖНО: сначала суммируем со знаком (расходы и возвраты/кредиты
    # взаимозачитываются), и только потом берём abs() от ИТОГА.
    # abs() каждой транзакции по отдельности завышает сумму, если
    # в категории есть и дебеты, и кредиты (см. баг, пойманный тестом).
    capex = abs(sum(t.amount for t in active if t.category == "capex"))
    opex = abs(sum(t.amount for t in active if t.category == "opex"))
    rent = abs(sum(t.amount for t in active if t.category == "rent"))
    denominator = opex + rent
    if denominator == 0:
        raise ValueError("OpEx + Rent = 0, деление на ноль — проверь классификацию")
    return capex / denominator


def calc_revenue(transactions: list[TransactionRecord]) -> float:
    active = _active(transactions)
    return sum(t.amount for t in active if t.category == "revenue")


def calc_related_party_payments(
    transactions: list[TransactionRecord], related_party_names: list[str]
) -> float:
    active = _active(transactions)
    matched = [t for t in active if is_related_party(t.counterparty, related_party_names)]
    # Тот же принцип: сначала netting со знаком, потом abs от итога —
    # если у связанной стороны есть и платежи, и возвраты, они должны
    # взаимозачитываться, а не складываться по модулю.
    return abs(sum(t.amount for t in matched))


# Диспетчер: ключевые слова в metric_name (нижний регистр) -> функция расчёта.
# Функции related_party требуют доп. аргумент related_party_names — обрабатываются отдельно.
_DISPATCH = {
    ("capital", "intensity"): calc_capital_intensity_ratio,
    ("revenue",): calc_revenue,
}


def compute_actual(
    term: ContractTerm,
    transactions: list[TransactionRecord],
    related_party_names: list[str] | None = None,
) -> float | None:
    metric = term.metric_name.lower()

    if "related" in metric or "связан" in metric:
        if related_party_names is None:
            raise ValueError(
                f"{term.company_id} {term.clause_id}: метрика похожа на "
                f"related-party, но related_party_names не переданы"
            )
        return calc_related_party_payments(transactions, related_party_names)

    for keywords, func in _DISPATCH.items():
        if all(kw in metric for kw in keywords) or any(kw in metric for kw in keywords):
            return func(transactions)

    return None  # Неизвестная метрика — нужен ручной разбор, НЕ угадываем
