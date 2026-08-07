"""
audit_adjuster — вытаскивает корректировки из текста аудиторских примечаний
и применяет их к списку TransactionRecord ПЕРЕД тем, как computation_engine
что-либо посчитает.

Найденный на P1 паттерн (дословно из примечания 7 "Отсечение и начисления"):

  "Операция TXN-P1-0045 (счёт-фактура от 2025-08-12) относится к услугам,
   оказанным в период с 2026-01-15 по 2026-03-20."

Смысл: транзакция ФОРМАЛЬНО датирована 2025 годом в леджере, но по факту
относится к ДРУГОМУ ковенантному периоду — её нужно ИСКЛЮЧИТЬ из расчёта
за 2025 год (cut-off/accrual principle). Это чистый regex-паттерн,
не нужен LLM: формат "Операция TXN-XXX-XXXX ... относится к ... с DATE по DATE"
достаточно устойчив.

ВАЖНО: сама корректировка НЕ означает "это выручка" или "это расход" —
она просто говорит "этой транзакции тут не место в данном периоде".
Какая именно категория (revenue/opex/...) — определяет transaction_analyzer,
audit_adjuster только помечает excluded_by_audit=True, если период
транзакции по примечанию НЕ пересекается с ковенантным периодом компании.
"""
from __future__ import annotations

import re
from datetime import date

from schemas import TransactionRecord

CUTOFF_NOTE_RE = re.compile(
    r"Операция\s+(TXN-[\w-]+)\s*"
    r"\(счёт-фактура\s+от\s+(\d{4}-\d{2}-\d{2})\)\s*"
    r"относится\s+к\s+услугам,\s*оказанным\s+в\s+период\s*"
    r"с\s+(\d{4}-\d{2}-\d{2})\s+по\s+(\d{4}-\d{2}-\d{2})",
)


def extract_cutoff_adjustments(
    audit_text: str,
) -> dict[str, tuple[date, date, str]]:
    """
    Возвращает dict[txn_id, (service_period_start, service_period_end, raw_note)].
    """
    adjustments = {}
    for match in CUTOFF_NOTE_RE.finditer(audit_text):
        txn_id, invoice_date_str, start_str, end_str = match.groups()
        adjustments[txn_id] = (
            date.fromisoformat(start_str),
            date.fromisoformat(end_str),
            match.group(0),
        )
    return adjustments


def _periods_overlap(
    a_start: date, a_end: date, b_start: date, b_end: date
) -> bool:
    return a_start <= b_end and b_start <= a_end


def apply_adjustments(
    transactions: list[TransactionRecord],
    adjustments: dict[str, tuple[date, date, str]],
    covenant_period_start: date,
    covenant_period_end: date,
) -> list[TransactionRecord]:
    """
    Помечает excluded_by_audit=True для транзакций, чей ФАКТИЧЕСКИЙ период
    оказания услуги (из audit-примечания) не пересекается с ковенантным
    периодом компании — даже если дата транзакции в леджере формально
    попадает в этот период.
    """
    for txn in transactions:
        if txn.txn_id not in adjustments:
            continue
        service_start, service_end, raw_note = adjustments[txn.txn_id]
        if not _periods_overlap(
            service_start, service_end, covenant_period_start, covenant_period_end
        ):
            txn.excluded_by_audit = True
            txn.audit_note = raw_note
    return transactions


if __name__ == "__main__":
    from datetime import date as date_cls

    sample_audit_text = """
    Примечание 7 — Отсечение и начисления
    Выручка признаётся в том ковенантном периоде, в котором фактически
    оказаны услуги, независимо от даты счёта-фактуры и даты поступления
    денежных средств.
    (7.1) Операция TXN-P1-0045 (счёт-фактура от 2025-08-12) относится к
    услугам, оказанным в период с 2026-01-15 по 2026-03-20.
    Основание: Обследование причальной стенки проводится в первом квартале
    2026 года.
    """

    adjustments = extract_cutoff_adjustments(sample_audit_text)
    print("Найденные корректировки:")
    for txn_id, (start, end, note) in adjustments.items():
        print(f"  {txn_id}: фактический период услуги {start}..{end}")

    # Симулируем транзакцию TXN-P1-0045, как она выглядит в леджере
    test_txn = TransactionRecord(
        txn_id="TXN-P1-0045",
        account_id="ACC-7801",
        date=date_cls(2025, 8, 12),
        counterparty="Kaspi Marine Engineering LLP",
        description="Quay wall inspection and survey servicing contract",
        amount=-612884.19,
        currency="USD",
        category="opex",
    )

    result = apply_adjustments(
        [test_txn],
        adjustments,
        covenant_period_start=date_cls(2025, 1, 1),
        covenant_period_end=date_cls(2025, 12, 31),
    )

    print(f"\nTXN-P1-0045 excluded_by_audit = {result[0].excluded_by_audit}")
    print(f"audit_note = {result[0].audit_note!r}")
    assert result[0].excluded_by_audit is True, "Транзакция ДОЛЖНА быть исключена!"
    print("\n✅ Тест пройден: транзакция корректно исключена из периода 2025.")
