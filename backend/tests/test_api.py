"""Tests de bout en bout en mode mock (aucune clé API nécessaire)."""
import os

os.environ["LLM_PROVIDER"] = "mock"
os.environ["DATABASE_URL"] = "sqlite:///./test_agents.db"
os.environ["KB_BACKEND"] = "memory"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.privacy import mask, unmask  # noqa: E402

Base.metadata.drop_all(engine)
client = TestClient(app)
client.__enter__()  # déclenche l'événement startup (création des tables)


def test_masking_roundtrip():
    text = "Contact : jean.dupont@mail.fr ou 06 12 34 56 78"
    masked, mapping = mask(text)
    assert "jean.dupont" not in masked and "06 12" not in masked
    assert unmask(masked, mapping) == text


def test_email_flow_and_review():
    r = client.post("/agents/email", json={
        "sender": "client@exemple.fr", "subject": "Demande de devis",
        "body": "Bonjour, pouvez-vous m'envoyer un devis pour 10 chaises ?"})
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "devis" and body["status"] == "a_valider" and body["draft"]

    pending = client.get("/tasks").json()
    assert any(t["id"] == body["task_id"] for t in pending)

    r = client.post(f"/tasks/{body['task_id']}/review", json={"decision": "corrige", "review_seconds": 90})
    assert r.json()["status"] == "corrige"


def test_spam_is_ignored():
    r = client.post("/agents/email", json={"subject": "Félicitations", "body": "Cliquez ici, gagnez un iPhone"})
    assert r.json()["status"] == "ignore"


def test_document_extraction_and_validation():
    text = ("ACME SARL\nFACTURE N° FAC-001\nDate : 05/11/2024\n"
            "Total HT : 100,00\nTVA 20% : 20,00\nTotal TTC : 120,00")
    r = client.post("/agents/document/text", json={"filename": "fac.txt", "text": text}).json()
    assert r["fields"]["total_ttc"] == 120.0
    assert r["fields"]["date"] == "2024-11-05"
    assert r["errors"] == []


def test_metrics():
    m = client.get("/metrics").json()
    assert m["tasks_total"] >= 3
    assert m["error_rate"] == 1.0  # la seule tâche relue a été corrigée
    assert m["time_saved_hours"] > 0
