"""
kyc_linker — извлекает связанные стороны из KYC-досье (порог голосующих
прав ЧИТАЕТСЯ ИЗ ТЕКСТА каждого досье — он разный у разных компаний,
от 20.0% до 38.0%, а не фиксирован) и сопоставляет их с полем
counterparty в леджере.

КРИТИЧНО (найдено на P1): форматирование имени компании РАЗНОЕ в разных
документах — "Aktau Holdings LLP" в KYC, но "Aktau Holdings L.L.P."
в леджере. Точное сравнение строк здесь ломается. Нужна нормализация:
убрать точки и запятые, лишние пробелы, привести к одному регистру,
отбросить локационные суффиксы в скобках вида "(Turkistan point)".

КРИТИЧНО (найдено при разборе всех 12 KYC-досье): реальные PDF содержат
варианты форматирования, которые ломали более простую версию regex —
"Ertis Capital, LLP" (запятая перед суффиксом) и "Kazyna Capital LLP.\\n38.9%"
(точка сразу после суффикса перед переводом строки, из-за табличной
разбивки текста PyMuPDF). Оба случая теперь учтены в ENTITY_PERCENT_RE.
"""
from __future__ import annotations

import re

RELATED_PARTY_THRESHOLD = 20.0  # fallback, если порог не найден в тексте досье

# Кавычки, встречающиеся вокруг названий компаний в KYC-досье (прямые и
# кавычки-ёлочки/типографские) — например '"Saryarka Capital Partners" LLP'.
_QUOTE_CHARS = "\"'«»“”"

# Захватывает "Название компании ... XX.X%" — название заканчивается на
# распространённый корпоративный суффикс (LLP, JSC, Ltd, LLC, Inc, L.L.P.).
# Запятая и кавычки разрешены внутри названия ("Capital, LLP", '"Turan Capital" LLP'),
# точка разрешена в разделителе перед процентом (суффикс сразу за точкой,
# как "LLP.\n38.9%").
ENTITY_PERCENT_RE = re.compile(
    r"([A-ZА-ЯЁ][\w \.\-&," + _QUOTE_CHARS + r"]*?(?:LLP|L\.L\.P\.|JSC|Ltd|LLC|Inc|Trust|Group|Corp))"
    r"[ \t\-:\n.]*(\d{1,3}(?:\.\d+)?)\s*%",
)

# Порог голосующих прав, при котором организация признаётся связанной
# стороной — свой у каждой компании ("... владеет XX.X% и более
# голосующих прав, признаются связанными сторонами для целей Договора").
THRESHOLD_RE = re.compile(r"владеет\s+(\d{1,3}(?:\.\d+)?)\s*%\s+и\s+более")

# Локационные суффиксы вида "(Turkistan point)", "(Kyzylorda station)" —
# встречаются в леджере, но не в KYC-досье, мешают точному сравнению.
LOCATION_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


def normalize_name(name: str) -> str:
    name = LOCATION_SUFFIX_RE.sub("", name)
    name = name.replace(".", "").replace(",", "")
    for quote_char in ('"', "'", "«", "»", "“", "”"):
        name = name.replace(quote_char, "")
    name = re.sub(r"\s+", " ", name)
    return name.strip().lower()


def _extract_threshold(kyc_text: str) -> float:
    match = THRESHOLD_RE.search(kyc_text)
    return float(match.group(1)) if match else RELATED_PARTY_THRESHOLD


def extract_related_parties(kyc_text: str) -> list[tuple[str, float]]:
    """
    Возвращает список (название_компании, доля_%) только для долей
    >= порога, извлечённого из ЭТОГО ЖЕ текста досье — остальные
    упомянутые в KYC организации НЕ являются связанными сторонами
    для целей ковенанта 6.3.
    """
    threshold = _extract_threshold(kyc_text)
    related = []
    for match in ENTITY_PERCENT_RE.finditer(kyc_text):
        name, pct_str = match.group(1).strip(), match.group(2)
        for quote_char in ('"', "'", "«", "»", "“", "”"):
            name = name.replace(quote_char, "")
        name = name.strip()
        pct = float(pct_str)
        if pct >= threshold:
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
