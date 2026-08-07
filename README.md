# Halyk AI Challenge — AI-агент для проверки ковенантов

Агент читает кредитные договоры, банковские транзакции, аудиторские примечания 
и KYC-досье, чтобы определить статус (COMPLIANT/BREACH) финансовых ковенантов 
для 12 заёмщиков.

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Настройка API-ключа

Нужен один из двух ключей (в переменной окружения или в agent/.env):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# ИЛИ
export OPENROUTER_API_KEY=sk-or-...
```

## Запуск полного пайплайна

```bash
python3 agent/pipeline.py <путь_к_documents> <путь_к_master_ledger.csv> "<команда>" "<email>" submission.json
```

## Проверка результата (сверка с ground_truth, только для открытого датасета)

```bash
python3 eval/self_scorer.py submission.json <путь_к_ground_truth.json>
```

## Архитектура

Пайплайн состоит из 10 модулей в agent/, выполняющихся последовательно:

1. document_classifier — определяет тип документа и account_id по содержимому PDF
2. contract_selector — выбирает актуальный (не черновик) договор на компанию
3. scenario_mapper — сопоставляет account_id со scenario_id (P1-P10, B1, B4) через леджер
4. term_extractor — LLM извлекает условия ковенантов 6.1/6.2/6.3 из текста договора
5. transaction_analyzer — классифицирует транзакции (CapEx/OpEx/Rent/Revenue) по ключевым словам
6. kyc_linker — определяет связанных сторон (>=20% голосующих прав) для 6.3
7. audit_adjuster — применяет cut-off корректировки из аудиторских примечаний
8. computation_engine — детерминированно считает actual-значения (без LLM)
9. evaluator — сравнивает actual с порогом договора, определяет status
10. pipeline.py — оркестрирует всё вышеперечисленное, собирает submission.json

Принцип: LLM используется только для понимания текста документов (term_extractor). 
Все расчёты и сравнения — чистый детерминированный Python.
