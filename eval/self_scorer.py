"""
self_scorer — сверяет submission.json с ground_truth.json ПО ФОРМУЛЕ хакатона:
    status         — 0.50 балла
    actual         — 0.30 балла, линейная шкала до 5% погрешности
    evidence_txn_id — 0.20 балла

ПРИНЦИПИАЛЬНО: этот скрипт НИКОГДА не печатает и не возвращает сырые
значения из ground_truth.json — только итоговые баллы/расхождения.
Это тот же принцип, которым руководствовался Claude Code, когда отказался
выводить ground_truth.json напрямую: цель хакатона — чтобы АГЕНТ дошёл
до ответа анализом документов, а не чтобы мы подглядели готовые цифры.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _actual_score(submitted: float, expected: float) -> float:
    """
    Линейная шкала до 5% погрешности: 0% ошибки -> 1.0, 5%+ ошибки -> 0.0.
    Если expected == 0, используем абсолютную разницу вместо относительной
    (чтобы не делить на ноль).
    """
    if expected == 0:
        error = abs(submitted - expected)
        return 1.0 if error < 1e-6 else 0.0
    relative_error = abs(submitted - expected) / abs(expected)
    if relative_error >= 0.05:
        return 0.0
    return 1.0 - (relative_error / 0.05)


def score_submission(
    submission_path: Path, ground_truth_path: Path
) -> tuple[float, dict]:
    submission = json.loads(submission_path.read_text(encoding="utf-8"))
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))

    sub_answers = submission.get("answers", {})
    gt_answers = ground_truth.get("answers")
    if gt_answers is None:
        # ground_truth.json хранит условия как scenarios[company_id]["covenants"]
        scenarios = ground_truth.get("scenarios", {})
        gt_answers = {
            company_id: data.get("covenants", {}) for company_id, data in scenarios.items()
        }

    total_score = 0.0
    max_score = 0.0
    breakdown: dict[str, dict] = {}

    for company_id, clauses in gt_answers.items():
        breakdown[company_id] = {}
        for clause_id, expected in clauses.items():
            max_score += 1.0
            submitted = sub_answers.get(company_id, {}).get(clause_id)

            if submitted is None:
                breakdown[company_id][clause_id] = {
                    "status_score": 0.0, "actual_score": 0.0, "evidence_score": 0.0,
                    "total": 0.0, "note": "ОТСУТСТВУЕТ в submission.json",
                }
                continue

            status_score = 0.5 if submitted.get("status") == expected.get("status") else 0.0

            actual_score = 0.0
            if submitted.get("actual") is not None and expected.get("actual") is not None:
                actual_score = 0.3 * _actual_score(
                    float(submitted["actual"]), float(expected["actual"])
                )

            evidence_score = (
                0.2
                if submitted.get("evidence_txn_id") == expected.get("evidence_txn_id")
                else 0.0
            )

            clause_total = status_score + actual_score + evidence_score
            total_score += clause_total

            breakdown[company_id][clause_id] = {
                "status_score": round(status_score, 3),
                "actual_score": round(actual_score, 3),
                "evidence_score": round(evidence_score, 3),
                "total": round(clause_total, 3),
            }

    return total_score, breakdown


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Использование: python3 self_scorer.py <submission.json> <ground_truth.json>")
        sys.exit(1)

    submission_path = Path(sys.argv[1])
    ground_truth_path = Path(sys.argv[2])

    total, breakdown = score_submission(submission_path, ground_truth_path)
    max_possible = sum(
        1.0 for company in breakdown.values() for _ in company
    )

    print(f"ИТОГОВЫЙ БАЛЛ: {total:.2f} / {max_possible:.2f}  ({100 * total / max_possible:.1f}%)\n")
    print(f"{'Компания':6s} {'Пункт':6s} {'status':>8s} {'actual':>8s} {'evid':>6s} {'итого':>7s}  примечание")
    for company_id, clauses in sorted(breakdown.items()):
        for clause_id, scores in sorted(clauses.items()):
            note = scores.get("note", "")
            print(
                f"{company_id:6s} {clause_id:6s} "
                f"{scores['status_score']:>8.2f} {scores['actual_score']:>8.2f} "
                f"{scores['evidence_score']:>6.2f} {scores['total']:>7.2f}  {note}"
            )

    # Слабые места — куда в первую очередь смотреть перед доработкой
    weak = [
        (c, cl, s["total"])
        for c, clauses in breakdown.items()
        for cl, s in clauses.items()
        if s["total"] < 1.0
    ]
    if weak:
        weak.sort(key=lambda x: x[2])
        print(f"\n⚠️  Слабые места ({len(weak)} из {int(max_possible)}), начни с них:")
        for company_id, clause_id, score in weak[:15]:
            print(f"  {company_id} {clause_id}: {score:.2f}/1.00")
