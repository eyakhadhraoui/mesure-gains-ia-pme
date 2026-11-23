"""Calcul des gains et du ROI à partir de la mesure de référence et des résultats du pilote.

Exemple :
    python evaluation/kpi.py --emails-month 600 --docs-month 250 \
        --review-min-email 1.5 --review-min-doc 2 --automation-rate 0.8 \
        --cost-per-task-eur 0.01 --project-cost-eur 6000 --hosting-eur-month 40

Les valeurs du pilote peuvent être lues directement depuis l'API : --from-api http://localhost:8000
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"


def baseline_minutes(path: Path) -> dict[str, dict]:
    by_type: dict[str, list[float]] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_type.setdefault(row["task_type"], []).append(float(row["minutes"]))
    return {t: {"n": len(v), "mean": statistics.mean(v),
                "stdev": statistics.stdev(v) if len(v) > 1 else 0.0} for t, v in by_type.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=str(DATA / "baseline_times.csv"))
    ap.add_argument("--emails-month", type=float, default=600)
    ap.add_argument("--docs-month", type=float, default=250)
    ap.add_argument("--review-min-email", type=float, default=1.5, help="temps de relecture mesuré au pilote")
    ap.add_argument("--review-min-doc", type=float, default=2.0)
    ap.add_argument("--automation-rate", type=float, default=0.8)
    ap.add_argument("--cost-per-task-eur", type=float, default=0.01)
    ap.add_argument("--hourly-cost-eur", type=float, default=35.0, help="coût horaire chargé d'un collaborateur")
    ap.add_argument("--project-cost-eur", type=float, default=6000.0, help="coût de mise en place")
    ap.add_argument("--hosting-eur-month", type=float, default=40.0)
    ap.add_argument("--from-api", default=None, help="URL de l'API pour lire /metrics")
    a = ap.parse_args()

    if a.from_api:
        with urllib.request.urlopen(f"{a.from_api.rstrip('/')}/metrics") as r:
            m = json.load(r)
        print("Métriques du pilote lues depuis l'API :", json.dumps(m, ensure_ascii=False))
        if m.get("automation_rate") is not None:
            a.automation_rate = m["automation_rate"]
        if m.get("cost_per_task_eur") is not None:
            a.cost_per_task_eur = m["cost_per_task_eur"]

    base = baseline_minutes(Path(a.baseline))
    lines = []
    total_saved_min = 0.0
    for kind, volume, review in (("email", a.emails_month, a.review_min_email),
                                 ("document", a.docs_month, a.review_min_doc)):
        before = base[kind]["mean"]
        auto = volume * a.automation_rate
        manual = volume - auto
        after_min = auto * review + manual * before
        before_min = volume * before
        saved = before_min - after_min
        total_saved_min += saved
        lines.append((kind, volume, before, review, before_min / 60, after_min / 60, saved / 60,
                      saved / before_min if before_min else 0))

    saved_h = total_saved_min / 60
    saved_eur = saved_h * a.hourly_cost_eur
    ai_cost = (a.emails_month + a.docs_month) * a.automation_rate * a.cost_per_task_eur
    run_cost = ai_cost + a.hosting_eur_month
    net_month = saved_eur - run_cost
    roi_12 = (net_month * 12 - a.project_cost_eur) / a.project_cost_eur
    payback = a.project_cost_eur / net_month if net_month > 0 else float("inf")

    print("\nMesure de référence (chronométrage) :")
    for t, s in base.items():
        print(f"  {t:<9} n={s['n']}  moyenne {s['mean']:.1f} min  écart-type {s['stdev']:.1f}")
    print("\nPar mois :")
    print(f"  {'type':<9}{'volume':>8}{'avant/t':>9}{'après/t':>9}{'h avant':>9}{'h après':>9}{'h gagnées':>11}{'gain':>7}")
    for k, v, b, r, hb, ha, hs, p in lines:
        print(f"  {k:<9}{v:>8.0f}{b:>8.1f}m{r:>8.1f}m{hb:>9.1f}{ha:>9.1f}{hs:>11.1f}{p:>7.0%}")
    print(f"\n  Heures gagnées / mois     : {saved_h:.1f} h  ({saved_eur:,.0f} €)")
    print(f"  Coût IA / mois            : {ai_cost:,.2f} €")
    print(f"  Hébergement / mois        : {a.hosting_eur_month:,.2f} €")
    print(f"  Gain net / mois           : {net_month:,.0f} €")
    print(f"  ROI sur 12 mois           : {roi_12:.0%}")
    print(f"  Retour sur investissement : {payback:.1f} mois")


if __name__ == "__main__":
    main()
