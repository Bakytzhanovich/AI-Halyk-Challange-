"""
contract_selector — второй модуль пайплайна.

Вход: список ClassifiedDocument от document_classifier (весь корпус).
Задача: для каждого account_id, у которого есть договоры (doc_type == "contract"),
выбрать ОДИН актуальный — не черновик, с самым поздним covenant_period_end.

Ничего не решает про то, какой договор "правильный" по содержанию —
только по формальным признакам (is_draft, период). Если для account_id
нет ни одного не-draft договора — это КРИТИЧНАЯ проблема данных,
`select_contracts` не должен молча падать, а обязан прокричать warning
и пропустить компанию, а не выбрать черновик по умолчанию.
"""
from __future__ import annotations

from collections import defaultdict

from schemas import ClassifiedDocument


class ContractSelectionWarning(Exception):
    """Не исключение в смысле остановки пайплайна — используется для сбора warnings."""


def select_contracts(
    documents: list[ClassifiedDocument],
) -> tuple[dict[str, ClassifiedDocument], list[str]]:
    """
    Возвращает:
    - dict[account_id, ClassifiedDocument] — выбранный актуальный договор на компанию
    - list[str] — тексты предупреждений о проблемных account_id (0 или 2+ активных
      договора, отсутствие covenant_period для сравнения и т.п.)
    """
    warnings: list[str] = []
    by_account: dict[str, list[ClassifiedDocument]] = defaultdict(list)

    for doc in documents:
        if doc.doc_type != "contract":
            continue
        if not doc.account_id:
            warnings.append(
                f"Договор без account_id, пропущен: {doc.file_path}"
            )
            continue
        by_account[doc.account_id].append(doc)

    selected: dict[str, ClassifiedDocument] = {}

    for account_id, contracts in by_account.items():
        active = [c for c in contracts if not c.is_draft]

        if not active:
            warnings.append(
                f"{account_id}: нет ни одного НЕ-черновика среди "
                f"{len(contracts)} договоров — компания пропущена, нужна ручная проверка!"
            )
            continue

        if len(active) > 1:
            # Несколько "активных" — берём с самым поздним covenant_period_end.
            with_period = [c for c in active if c.covenant_period_end is not None]
            if not with_period:
                warnings.append(
                    f"{account_id}: {len(active)} не-черновиков, но ни у одного "
                    f"нет covenant_period_end — берём первый по списку, ПРОВЕРЬ ВРУЧНУЮ."
                )
                selected[account_id] = active[0]
                continue
            warnings.append(
                f"{account_id}: найдено {len(active)} не-черновиков одновременно — "
                f"выбран с самым поздним covenant_period_end, но это нетипично, ПРОВЕРЬ."
            )
            selected[account_id] = max(with_period, key=lambda c: c.covenant_period_end)
            continue

        selected[account_id] = active[0]

    for doc in selected.values():
        doc.is_authoritative = True

    return selected, warnings


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from document_classifier import classify_all

    docs_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "documents")
    all_docs = classify_all(docs_dir)

    selected, warnings = select_contracts(all_docs)

    print(f"Выбрано актуальных договоров: {len(selected)}")
    for account_id, doc in sorted(selected.items()):
        print(f"  {account_id}: {Path(doc.file_path).name}")

    if warnings:
        print(f"\n⚠️  Предупреждения ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("\n✅ Проблем не найдено — на каждый account_id ровно один активный договор.")
