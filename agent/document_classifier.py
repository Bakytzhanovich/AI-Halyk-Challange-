"""
document_classifier — первый модуль пайплайна.

Читает файл из documents/ (PDF, TXT или CSV) и определяет:
- тип документа (contract / audit / kyc / other)
- account_id, к которому он относится (если удаётся найти)
- является ли договор черновиком/недействующей редакцией
- ковенантный период (если это договор)

Имена файлов — случайные хеши, поэтому классификация идёт
ТОЛЬКО по содержимому, не по filename.

Найденные в датасете сигналы (см. разбор P1):
- маркер черновика: "НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ" / "НЕ ПРИМЕНЯЕТСЯ"
- account_id: паттерн "ACC-XXXX" (4 цифры), иногда с суффиксом "-NN"
  — суффиксные account_id (ACC-7801-05 и т.п.) встречались в
  документах-дистракторах (weekly report, generic compliance
  procedure) и НЕ совпадали с account_id из леджера — их не стоит
  путать с реальным account_id компании.
- ковенантный период: "с YYYY-MM-DD по YYYY-MM-DD" внутри статьи 6
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import fitz  # PyMuPDF

from schemas import ClassifiedDocument

# --- Сигнатуры типов документов (эвристика первого прохода;
#     неочевидные случаи в проде стоит дозавернуть в LLM-классификатор) ---

DRAFT_MARKERS = [
    "недействующая редакция",
    "не применяется",
    "черновик",
    "draft",
]

KYC_MARKERS = [
    "знай своего клиента",
    "kyc",
    "бенефициарное владение",
    "источник средств",
    "санкционные проверки",
]

AUDIT_MARKERS = [
    "аудитор",
    "примечани",  # "примечание к отчётности"
    "аудированной финансовой отчётности",
]

CONTRACT_MARKERS = [
    "договор банковского займа",
    "финансовые ковенанты",
    "заёмщик",
    "кредитор",
]

# Паттерны для structured extraction
ACCOUNT_ID_RE = re.compile(r"ACC-(\d{4})(?:-\d+)?")
COVENANT_PERIOD_RE = re.compile(
    r"период[а-я\s]*с\s+(\d{4}-\d{2}-\d{2})\s+по\s+(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def _extract_text(file_path: Path) -> str:
    if file_path.suffix.lower() == ".pdf":
        with fitz.open(file_path) as doc:
            return "\n".join(page.get_text() for page in doc)
    elif file_path.suffix.lower() in (".txt", ".csv"):
        return file_path.read_text(errors="ignore")
    return ""


def _classify_doc_type(text_lower: str) -> str:
    """
    Считаем ВХОЖДЕНИЯ маркеров, а не только их наличие (0/1).
    Наличие-based скоринг даёт ложные ничьи: аудиторская записка,
    упоминающая "заёмщика"/"кредитора" пару раз (обычная лексика
    в примечаниях к отчётности по кредиту), набирала contract=2
    наравне с audit=2 — хотя "аудитор"/"примечани" встречались в
    ней 16 раз против 3 у контрактных маркеров. Частотный скоринг
    убирает эту ничью, не трогая ни одну из остальных 201 классификаций.
    """
    scores = {
        "contract": sum(text_lower.count(m) for m in CONTRACT_MARKERS),
        "kyc": sum(text_lower.count(m) for m in KYC_MARKERS),
        "audit": sum(text_lower.count(m) for m in AUDIT_MARKERS),
    }
    best_type, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_type if best_score > 0 else "other"


def _extract_account_id(text: str) -> str | None:
    """
    Берём самый частотный ACC-XXXX (без суффикса) в документе —
    это устойчивее, чем "первое совпадение", т.к. дистракторные
    суффиксные ID (ACC-7801-05) обычно встречаются один раз,
    а основной ID компании — многократно по всему договору/досье.
    """
    matches = ACCOUNT_ID_RE.findall(text)
    if not matches:
        return None
    from collections import Counter

    most_common = Counter(matches).most_common(1)[0][0]
    return f"ACC-{most_common}"


def _extract_covenant_period(text: str) -> tuple[date | None, date | None]:
    match = COVENANT_PERIOD_RE.search(text)
    if not match:
        return None, None
    start = date.fromisoformat(match.group(1))
    end = date.fromisoformat(match.group(2))
    return start, end


def classify_document(file_path: Path) -> ClassifiedDocument:
    text = _extract_text(file_path)
    text_lower = text.lower()

    doc_type = _classify_doc_type(text_lower)
    is_draft = doc_type == "contract" and any(m in text_lower for m in DRAFT_MARKERS)
    account_id = _extract_account_id(text)
    period_start, period_end = (
        _extract_covenant_period(text) if doc_type == "contract" else (None, None)
    )

    return ClassifiedDocument(
        file_path=str(file_path),
        doc_type=doc_type,  # type: ignore[arg-type]
        account_id=account_id,
        is_draft=is_draft,
        covenant_period_start=period_start,
        covenant_period_end=period_end,
        raw_text_excerpt=text[:500],
        confidence=0.6 if doc_type == "other" else 0.9,
    )


def classify_all(documents_dir: Path) -> list[ClassifiedDocument]:
    results = []
    for path in sorted(documents_dir.iterdir()):
        if path.suffix.lower() not in (".pdf", ".txt", ".csv"):
            continue  # пропускаем Thumbs.db, .DS_Store и т.п.
        try:
            results.append(classify_document(path))
        except Exception as e:  # не роняем весь батч из-за одного битого файла
            print(f"[document_classifier] WARN: failed on {path.name}: {e}")
    return results


if __name__ == "__main__":
    import sys

    docs_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "documents")
    for doc in classify_all(docs_dir):
        flag = " [DRAFT]" if doc.is_draft else ""
        print(
            f"{Path(doc.file_path).name:20s} "
            f"type={doc.doc_type:9s} account={doc.account_id or '-':12s}"
            f"{flag}"
        )
