"""
scenario_mapper — маленький, но критичный мост между двумя мирами:
- documents/ (и наши модули) оперируют account_id (ACC-XXXX)
- submission.json требует ключи scenario_id (P1..P10, B1, B4)

Источник истины — master_ledger_2025.csv: строки вида
TXN-P1-0007,...,ACC-7801,... содержат оба идентификатора одновременно.
Берём МАЖОРИТАРНОЕ голосование (Counter.most_common), а не первое
попавшееся совпадение — устойчивее к единичным опечаткам/аномалиям
в леджере, если они вдруг есть.

Транзакции-шум (TXN-9001-0036 и т.п., account_id вроде ACC-9001,
не относящийся ни к одному из 12 заёмщиков) просто не матчатся
регуляркой SCENARIO_PREFIX_RE и не попадают в маппинг — это ожидаемо.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

SCENARIO_PREFIX_RE = re.compile(r"^TXN-(P\d{1,2}|B\d{1,2})-")


def build_mapping(ledger_path: Path) -> tuple[dict[str, str], list[str]]:
    """
    Возвращает:
    - dict[account_id, scenario_id] — итоговый маппинг
    - list[str] — предупреждения (scenario с >1 account_id, account_id с >1 scenario)
    """
    votes: dict[str, Counter] = defaultdict(Counter)  # scenario_id -> Counter[account_id]

    with open(ledger_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            match = SCENARIO_PREFIX_RE.match(row["txn_id"])
            if not match:
                continue  # шумовая транзакция, не из 12 заёмщиков
            scenario_id = match.group(1)
            account_id = row["account_id"]
            votes[scenario_id][account_id] += 1

    warnings: list[str] = []
    account_to_scenario: dict[str, str] = {}
    scenario_to_account: dict[str, str] = {}

    for scenario_id, counter in votes.items():
        if len(counter) > 1:
            warnings.append(
                f"{scenario_id}: транзакции ссылаются на {len(counter)} разных "
                f"account_id ({dict(counter)}) — берём мажоритарный, ПРОВЕРЬ ВРУЧНУЮ."
            )
        account_id = counter.most_common(1)[0][0]
        scenario_to_account[scenario_id] = account_id
        account_to_scenario[account_id] = scenario_id

    # Обратная проверка: один account_id не должен принадлежать двум scenario_id
    reverse_check: dict[str, list[str]] = defaultdict(list)
    for scenario_id, account_id in scenario_to_account.items():
        reverse_check[account_id].append(scenario_id)
    for account_id, scenarios in reverse_check.items():
        if len(scenarios) > 1:
            warnings.append(
                f"{account_id}: сопоставлен НЕСКОЛЬКИМ scenario_id {scenarios} — "
                f"это противоречие в данных, ПРОВЕРЬ ВРУЧНУЮ."
            )

    return account_to_scenario, warnings


if __name__ == "__main__":
    import sys

    ledger_path = Path(sys.argv[1] if len(sys.argv) > 1 else "master_ledger_2025.csv")
    mapping, warnings = build_mapping(ledger_path)

    print(f"Найдено сценариев: {len(mapping)}")
    for account_id, scenario_id in sorted(mapping.items(), key=lambda kv: kv[1]):
        print(f"  {scenario_id:4s} -> {account_id}")

    expected = {f"P{i}" for i in range(1, 11)} | {"B1", "B4"}
    found = set(mapping.values())
    missing = expected - found
    unexpected = found - expected
    if missing:
        print(f"\n⚠️  Не найдены в леджере: {sorted(missing)}")
    if unexpected:
        print(f"⚠️  Неожиданные scenario_id (не входят в P1-P10/B1/B4): {sorted(unexpected)}")

    if warnings:
        print(f"\n⚠️  Предупреждения ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")
    elif not missing and not unexpected:
        print("\n✅ Все 12 сценариев найдены, конфликтов нет.")
