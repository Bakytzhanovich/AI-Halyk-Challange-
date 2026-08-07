"""
kyc_linker — извлекает связанные стороны из KYC-досье (порог >= 20.0%
голосующих прав, как явно указано в разделе "Бенефициарное владение и
контроль") и сопоставляет их с полем counterparty в леджере.

КРИТИЧНО (найдено на P1): форматирование имени компании РАЗНОЕ в разных
документах — "Aktau Holdings LLP" в KYC, но "Aktau Holdings L.L.P."
в леджере. Точное сравнение строк здесь ломается. Нужна нормализация:
убрать точки, лишние пробелы, привести к одному регистру, отбросить
локационные суффиксы в скобках вида "(Turkistan point)".
"""
from __future__ import annotations

import re

RELATED_PARTY_THRESHOLD = 20.0  # % голосующих прав — порог из формулировки договора

# Захватывает "Название компании ... XX.X%" — название заканчивается на
# распространённый корпоративный суффикс (LLP, JSC, Ltd, LLC, Inc, L.L.P.)
ENTITY_PERCENT_RE = re.compile(
    r"([A-ZА-ЯЁ][\w \.\-&]*?(?:LLP|L\.L\.P\.|JSC|Ltd|LLC|Inc|Trust|Group|Corp))"
    r"[ \t\-:]*(\d{1,3}(?:\.\d+)?)\s*%",
)

# Локационные суффиксы вида "(Turkistan point)", "(Kyzylorda station)" —
# встречаются в леджере, но не в KYC-досье, мешают точному сравнению.
LOCATION_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


def normalize_name(name: str) -> str:
    name = LOCATION_SUFFIX_RE.sub("", name)
    name = name.replace(".", "")
    name = re.sub(r"\s+", " ", name)
    return name.strip().lower()


def extract_related_parties(kyc_text: str) -> list[tuple[str, float]]:
    """
    Возвращает список (название_компании, доля_%) только для долей
    >= RELATED_PARTY_THRESHOLD — остальные упомянутые в KYC организации
    НЕ являются связанными сторонами для целей ковенанта 6.3.
    """
    related = []
    for match in ENTITY_PERCENT_RE.finditer(kyc_text):
        name, pct_str = match.group(1).strip(), match.group(2)
        pct = float(pct_str)
        if pct >= RELATED_PARTY_THRESHOLD:
            related.append((name, pct))
    return related


def is_related_party(counterparty: str, related_party_names: list[str]) -> bool:
    """
    True, если counterparty (из леджера) совпадает с одной из связанных
    сторон после нормализации. Сравнение по вхождению нормализованной
    строки в обе стороны — устойчиво к небольшим вариациям окончаний
    (LLP/L.L.P., лишние организационные суффиксы вроде "Enterprise"/"Holdings").
    """
    norm_counterparty = normalize_name(counterparty)
    for rp_name in related_party_names:
        norm_rp = normalize_name(rp_name)
        if norm_rp in norm_counterparty or norm_counterparty in norm_rp:
            return True
    return False


if __name__ == "__main__":
    # Реальный текст раздела KYC-досье P1 (Aktau Port Services JSC),
    # как его вывел Claude Code дословно.
    sample_kyc_text = """
    Бенефициарное владение и контроль
    Ниже приведены прямые и косвенные доли участия Группы в организациях-контрагентах
    по состоянию на дату проверки.
    Организация	Доля голосующих прав
    Aktau Holdings LLP	34.5%
    Kaspi Marine Engineering LLP	18.7%
    Ural Crane Works LLP	6.2%
    Организации, в которых Группа владеет 20.0% и более голосующих прав,
    признаются связанными сторонами для целей Договора.
    """

    related = extract_related_parties(sample_kyc_text)
    print("Связанные стороны (>= 20.0%):")
    for name, pct in related:
        print(f"  {name} — {pct}%")

    related_names = [name for name, _ in related]

    # Реальные контрагенты из леджера P1 — проверяем матчинг
    test_counterparties = [
        "Aktau Holdings L.L.P.",             # должен совпасть (несмотря на точки)
        "Kaspi Marine Engineering LLP",       # НЕ должен совпасть (18.7% < 20%)
        "Ural Crane Works LLP",               # НЕ должен совпасть (6.2% < 20%)
        "Hartley Building Services Holdings (Turkistan point)",  # НЕ должен совпасть
    ]
    print("\nПроверка матчинга:")
    for cp in test_counterparties:
        result = is_related_party(cp, related_names)
        print(f"  {cp!r:55s} -> {'СВЯЗАННАЯ СТОРОНА' if result else 'нет'}")
