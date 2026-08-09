"""
term_extractor — читает Статью 6 «Финансовые ковенанты» договора через LLM
и извлекает СТРУКТУРИРОВАННЫЕ условия (ContractTerm) для пунктов 6.1/6.2/6.3.

КРИТИЧНО: промпт не должен предполагать конкретные формулировки P1.
У каждого из 12 договоров своя метрика/операция/период — модель должна
извлекать их из текста заново на каждый вызов, не по шаблону.

Поддерживает ТРИ способа доступа к модели (выбирается автоматически по
тому, какая переменная окружения установлена, приоритет сверху вниз):

  export ANTHROPIC_API_KEY=sk-ant-...      # напрямую через Anthropic API
  export OPENAI_API_KEY=sk-...             # напрямую через OpenAI API
  export OPENROUTER_API_KEY=sk-or-...      # через OpenRouter
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import fitz

from schemas import ContractTerm

MODEL = "claude-sonnet-4-6"                       # для прямого Anthropic API
OPENROUTER_MODEL = "anthropic/claude-sonnet-4.6"   # тот же Sonnet, но через OpenRouter
OPENAI_MODEL = "gpt-4.1-mini"                       # для прямого OpenAI API


def make_llm_client():
    """
    Возвращает callable(system_prompt, user_content) -> str (сырой текстовый
    ответ модели), автоматически выбирая backend по переменным окружения.
    Приоритет: ANTHROPIC_API_KEY -> OPENAI_API_KEY (напрямую) -> OPENROUTER_API_KEY.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")

    if anthropic_key:
        import anthropic

        client = anthropic.Anthropic(api_key=anthropic_key)

        def call(system_prompt: str, user_content: str) -> str:
            response = client.messages.create(
                model=MODEL,
                max_tokens=2000,
                temperature=0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            return response.content[0].text

        return call

    if openai_key:
        from openai import OpenAI

        client = OpenAI(api_key=openai_key)  # без base_url — идёт напрямую в api.openai.com

        def call(system_prompt: str, user_content: str) -> str:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=2000,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            return response.choices[0].message.content

        return call

    if openrouter_key:
        from openai import OpenAI

        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_key)

        def call(system_prompt: str, user_content: str) -> str:
            response = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                max_tokens=2000,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
            return response.choices[0].message.content

        return call

    raise RuntimeError(
        "Не найден ни ANTHROPIC_API_KEY, ни OPENAI_API_KEY, ни OPENROUTER_API_KEY "
        "в окружении. Установи один из них."
    )


SYSTEM_PROMPT = """Ты — финансовый аналитик, который извлекает условия ковенантов \
из кредитных договоров. Твоя задача — читать ТОЛЬКО текст, который тебе дали, \
и извлекать условия ТОЧНО так, как они сформулированы в договоре — никогда не \
додумывай, не обобщай и не подставляй типовые формулировки из других договоров.

Правила:
1. Ищи Статью 6 «Финансовые ковенанты» (или аналогичный раздел с пунктами вида X.1, X.2, X.3).
2. Для КАЖДОГО найденного пункта извлеки:
   - clause_id — номер пункта ровно как в договоре (обычно "6.1", "6.2", "6.3")
   - metric_name — короткое машиночитаемое имя метрики на английском (snake_case), например "capital_intensity_ratio"
   - operator — ">=" если условие "не менее/не ниже", "<=" если "не должен превышать/не выше", "<" или ">" если строгое неравенство явно указано словом "строго"
   - threshold — числовое значение порога, без единиц измерения
   - currency — валюта порога, если это денежная сумма (иначе null)
   - period_start, period_end — даты ковенантного периода в формате YYYY-MM-DD
   - formula_description — ДОСЛОВНОЕ или максимально близкое к тексту описание того, что входит в расчёт метрики (не сокращай, не обобщай — если в договоре есть оговорка про переклассификацию аудитором, обязательно включи её)
   - source_span — короткая цитата (до 20 слов) или указание номера пункта, откуда взято условие
3. Если каких-то пунктов нет или их формулировка сильно отличается от типовой — извлеки то, что есть, не изобретай недостающее.
4. Если валюта не указана явно для порога — оставь currency = null.

Верни СТРОГО валидный JSON-массив объектов с полями:
clause_id, metric_name, operator, threshold, currency, period_start, period_end, formula_description, source_span

Никакого текста до или после JSON. Никаких markdown-блоков ```."""


ARTICLE_6_START_RE = re.compile(r"Статья\s+6\b", re.IGNORECASE)
ARTICLE_6_HEADING_RE = re.compile(r"Статья\s+6\s*[—-]", re.IGNORECASE)
ARTICLE_NEXT_RE = re.compile(r"Статья\s+7\s*[—-]", re.IGNORECASE)


def _extract_article_6_context(full_text: str) -> str:
    """
    Документы содержат оглавление в начале ("Статья 6\\nФинансовые ковенанты",
    без тире) ПЕРЕД реальным заголовком раздела ("Статья 6 — Финансовые
    ковенанты", с тире) — берём именно заголовок раздела, а не первое
    совпадение "Статья 6", иначе попадаем в оглавление и остаёмся без текста.
    """
    start_match = ARTICLE_6_HEADING_RE.search(full_text)
    if not start_match:
        start_match = ARTICLE_6_START_RE.search(full_text)
    if not start_match:
        return full_text

    start = start_match.start()
    end_match = ARTICLE_NEXT_RE.search(full_text, start_match.end())
    end = end_match.start() if end_match else len(full_text)
    return full_text[start:end]


def extract_terms_from_contract(
    pdf_path: Path,
    company_id: str,
    call_llm,
) -> tuple[list[ContractTerm], list[str]]:
    with fitz.open(pdf_path) as doc:
        full_text = "\n".join(page.get_text() for page in doc)

    context = _extract_article_6_context(full_text)

    raw = call_llm(SYSTEM_PROMPT, f"Текст договора:\n\n{context}").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0] if "```" in raw else raw

    parsed = json.loads(raw)

    terms = []
    item_warnings = []
    for item in parsed:
        try:
            terms.append(
                ContractTerm(
                    company_id=company_id,
                    clause_id=item["clause_id"],
                    metric_name=item["metric_name"],
                    operator=item["operator"],
                    threshold=float(item["threshold"]),
                    currency=item.get("currency"),
                    period_start=item["period_start"],
                    period_end=item["period_end"],
                    formula_description=item["formula_description"],
                    source_doc=str(pdf_path),
                    source_span=item.get("source_span"),
                )
            )
        except Exception as e:
            item_warnings.append(
                f"{company_id}: не удалось разобрать пункт "
                f"{item.get('clause_id', '?')} — {e}"
            )
    return terms, item_warnings


def extract_all(
    selected_contracts: dict[str, Path],
    account_to_scenario: dict[str, str],
) -> tuple[list[ContractTerm], list[str]]:
    call_llm = make_llm_client()
    all_terms: list[ContractTerm] = []
    warnings: list[str] = []

    for account_id, pdf_path in selected_contracts.items():
        company_id = account_to_scenario.get(account_id)
        if not company_id:
            warnings.append(f"{account_id}: нет scenario_id в маппинге, пропущен")
            continue
        try:
            terms, item_warnings = extract_terms_from_contract(pdf_path, company_id, call_llm)
            warnings.extend(item_warnings)
            if len(terms) != 3:
                warnings.append(
                    f"{company_id} ({account_id}): извлечено {len(terms)} условий "
                    f"вместо ожидаемых 3 — ПРОВЕРЬ ВРУЧНУЮ"
                )
            all_terms.extend(terms)
        except Exception as e:
            warnings.append(f"{company_id} ({account_id}): ошибка извлечения — {e}")

    return all_terms, warnings


if __name__ == "__main__":
    import sys

    from contract_selector import select_contracts
    from document_classifier import classify_all
    from scenario_mapper import build_mapping

    documents_dir = Path(sys.argv[1])
    ledger_path = Path(sys.argv[2])

    all_docs = classify_all(documents_dir)
    selected_docs, sel_warnings = select_contracts(all_docs)
    account_to_scenario, map_warnings = build_mapping(ledger_path)

    selected_paths = {
        account_id: Path(doc.file_path) for account_id, doc in selected_docs.items()
    }

    terms, extract_warnings = extract_all(selected_paths, account_to_scenario)

    print(f"Извлечено условий: {len(terms)} (ожидалось {len(selected_paths) * 3})")
    for t in terms:
        print(
            f"  {t.company_id} {t.clause_id}: {t.metric_name} {t.operator} "
            f"{t.threshold} {t.currency or ''} [{t.period_start}..{t.period_end}]"
        )

    all_warnings = sel_warnings + map_warnings + extract_warnings
    if all_warnings:
        print(f"\n⚠️  Предупреждения ({len(all_warnings)}):")
        for w in all_warnings:
            print(f"  - {w}")
