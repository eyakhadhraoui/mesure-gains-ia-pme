"""Évaluation hors ligne des agents sur le jeu de test étiqueté.

Exemples :
    python evaluation/evaluate.py --provider mock      # baseline par règles, gratuit
    python evaluation/evaluate.py --provider gemini    # nécessite GEMINI_API_KEY
    python evaluation/evaluate.py --provider claude    # nécessite ANTHROPIC_API_KEY
    python evaluation/evaluate.py --provider openai    # nécessite OPENAI_API_KEY
    CLASSIFY_MODEL=claude-haiku-5-5 python evaluation/evaluate.py --provider claude

Mesure : précision de classification (globale, par classe, matrice de confusion),
exactitude d'extraction par champ, coût et latence par tâche.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.document_agent import EXTRACT_SCHEMA, EXTRACT_SYSTEM  # noqa: E402
from app.agents.email_agent import CATEGORIES, CLASSIFY_SCHEMA, CLASSIFY_SYSTEM  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import PRICES_USD_PER_MTOK, LLMResult, get_provider  # noqa: E402
from app.privacy import mask  # noqa: E402

DATA = Path(__file__).parent / "data"
FIELDS = ["document_type", "supplier", "document_number", "date", "total_ht", "tva", "total_ttc"]


def safe_complete(llm, task, system, text, schema, model):
    """Un appel en erreur (réseau, quota) compte comme un échec au lieu d'arrêter l'évaluation."""
    try:
        return llm.complete(task, system, text, schema=schema, model=model, effort="low")
    except Exception as e:  # noqa: BLE001
        print(f"  ! erreur {task} : {str(e)[:120]}")
        return LLMResult(task, llm.name, model or "?", None, refused=True)


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (DATA / name).read_text(encoding="utf-8").splitlines() if l.strip()]


def same(field: str, got, exp) -> bool:
    if isinstance(exp, (int, float)):
        try:
            return got is not None and abs(float(got) - exp) <= 0.01
        except (TypeError, ValueError):
            return False
    return str(got or "").strip().casefold() == str(exp).strip().casefold()


def eval_emails(llm, limit: int | None) -> dict:
    rows, calls = [], []
    for e in load("emails_test.jsonl")[:limit]:
        text, _ = mask(f"De : {e['sender']}\nObjet : {e['subject']}\n\n{e['body']}")
        r = safe_complete(llm, "classify_email", CLASSIFY_SYSTEM, text, CLASSIFY_SCHEMA,
                          settings.classify_model)
        pred = (r.data or {}).get("category", "refus") if not r.refused else "refus"
        rows.append({"id": e["id"], "label": e["label"], "pred": pred})
        calls.append(r)
    ok = sum(r["label"] == r["pred"] for r in rows)
    per_class = {}
    for c in CATEGORIES:
        tp = sum(r["label"] == c and r["pred"] == c for r in rows)
        fp = sum(r["label"] != c and r["pred"] == c for r in rows)
        fn = sum(r["label"] == c and r["pred"] != c for r in rows)
        p = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        per_class[c] = {"precision": round(p, 3), "recall": round(rc, 3),
                        "f1": round(2 * p * rc / (p + rc), 3) if p + rc else 0.0}
    confusion = Counter((r["label"], r["pred"]) for r in rows)
    return {"n": len(rows), "accuracy": round(ok / len(rows), 3), "per_class": per_class,
            "confusion": {f"{a}->{b}": n for (a, b), n in sorted(confusion.items()) if a != b},
            "errors": [r for r in rows if r["label"] != r["pred"]], "_calls": calls}


def eval_documents(llm, limit: int | None) -> dict:
    field_ok, rows, calls = defaultdict(int), [], []
    docs = load("documents_test.jsonl")[:limit]
    for d in docs:
        text, _ = mask(d["text"])
        r = safe_complete(llm, "extract_document", EXTRACT_SYSTEM, text, EXTRACT_SCHEMA,
                          settings.extract_model)
        got = r.data or {}
        wrong = {f: {"attendu": d["expected"][f], "obtenu": got.get(f)}
                 for f in FIELDS if not same(f, got.get(f), d["expected"][f])}
        for f in FIELDS:
            field_ok[f] += f not in wrong
        rows.append({"id": d["id"], "wrong": wrong})
        calls.append(r)
    n = len(docs)
    total_ok = sum(field_ok.values())
    return {"n": n, "field_accuracy": round(total_ok / (n * len(FIELDS)), 3),
            "per_field": {f: round(field_ok[f] / n, 3) for f in FIELDS},
            "fully_correct_docs": sum(not r["wrong"] for r in rows),
            "errors": [r for r in rows if r["wrong"]], "_calls": calls}


def cost_summary(calls) -> dict:
    n = len(calls) or 1
    cost = sum(c.cost_usd for c in calls)
    return {"calls": len(calls), "cost_usd": round(cost, 5), "cost_per_task_usd": round(cost / n, 6),
            "cost_per_task_eur": round(cost / n * settings.usd_to_eur, 6),
            "avg_latency_ms": round(sum(c.latency_ms for c in calls) / n),
            "input_tokens": sum(c.input_tokens for c in calls),
            "output_tokens": sum(c.output_tokens for c in calls),
            "refusals": sum(c.refused for c in calls),
            "models": sorted({c.model for c in calls}),
            # Modèles absents de PRICES_USD_PER_MTOK : leur coût n'est PAS compté
            "unpriced_models": sorted({c.model for c in calls} - set(PRICES_USD_PER_MTOK))}


def cost_line(c: dict) -> str:
    if c["unpriced_models"]:
        return (f"prix à renseigner pour {', '.join(c['unpriced_models'])} dans backend/app/llm.py "
                f"({c['input_tokens']} tokens entrée, {c['output_tokens']} tokens sortie)")
    return f"{c['cost_per_task_eur']:.5f} €"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock", choices=["mock", "gemini", "claude", "openai"])
    ap.add_argument("--limit", type=int, default=None, help="limiter le nombre d'exemples (tests rapides)")
    ap.add_argument("--only", choices=["emails", "documents"], default=None)
    args = ap.parse_args()

    llm = get_provider(args.provider)
    report: dict = {"provider": args.provider, "date": time.strftime("%Y-%m-%d %H:%M")}
    if args.only != "documents":
        em = eval_emails(llm, args.limit)
        em["cost"] = cost_summary(em.pop("_calls"))
        report["emails"] = em
    if args.only != "emails":
        dc = eval_documents(llm, args.limit)
        dc["cost"] = cost_summary(dc.pop("_calls"))
        report["documents"] = dc

    out = Path(__file__).parent / "results"
    out.mkdir(exist_ok=True)
    path = out / f"{args.provider}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== Évaluation — fournisseur : {args.provider} ===")
    if "emails" in report:
        e = report["emails"]
        print(f"\nClassification des emails (n={e['n']}) : précision globale {e['accuracy']:.1%}")
        for c, m in e["per_class"].items():
            print(f"  {c:<12} P={m['precision']:.2f}  R={m['recall']:.2f}  F1={m['f1']:.2f}")
        if e["confusion"]:
            print("  Confusions :", ", ".join(f"{k} ({v})" for k, v in e["confusion"].items()))
        print(f"  Coût/email : {cost_line(e['cost'])} — latence moy. {e['cost']['avg_latency_ms']} ms")
    if "documents" in report:
        d = report["documents"]
        print(f"\nExtraction de documents (n={d['n']}) : exactitude par champ {d['field_accuracy']:.1%}, "
              f"documents 100 % corrects : {d['fully_correct_docs']}/{d['n']}")
        for f, a in d["per_field"].items():
            print(f"  {f:<16} {a:.0%}")
        print(f"  Coût/document : {cost_line(d['cost'])} — latence moy. {d['cost']['avg_latency_ms']} ms")
    print(f"\nRapport détaillé : {path}")


if __name__ == "__main__":
    main()
