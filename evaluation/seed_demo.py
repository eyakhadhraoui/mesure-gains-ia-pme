"""Envoie des emails et documents du jeu de test à l'API pour remplir la file de validation.

    python evaluation/seed_demo.py                 # 12 emails + 4 documents
    python evaluation/seed_demo.py --emails 40 --docs 10 --api http://localhost:8000
"""
import argparse
import json
import os
import urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"


def post(api: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(f"{api}{path}", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json",
                                          "X-API-Key": os.getenv("API_KEY", "")})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--emails", type=int, default=12)
    ap.add_argument("--docs", type=int, default=4)
    a = ap.parse_args()

    emails = [json.loads(l) for l in (DATA / "emails_test.jsonl").read_text(encoding="utf-8").splitlines() if l]
    # On mélange les catégories : un email sur trois du jeu de test
    for e in (emails[::3] + emails[1::3])[:a.emails]:
        r = post(a.api, "/agents/email", {"sender": e["sender"], "subject": e["subject"],
                                          "body": e["body"], "message_id": e["id"]})
        print(f"email {e['id']:<4} -> {r['category'] or '?':<12} {r['status']}")

    docs = [json.loads(l) for l in (DATA / "documents_test.jsonl").read_text(encoding="utf-8").splitlines() if l]
    for d in docs[:a.docs]:
        r = post(a.api, "/agents/document/text", {"filename": f"{d['id']}.txt", "text": d["text"]})
        print(f"doc   {d['id']:<4} -> {r['fields'].get('document_type', '?'):<12} {r['status']}")


if __name__ == "__main__":
    main()
