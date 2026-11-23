"""API FastAPI : point d'entrée unique appelé par n8n et par l'interface Angular."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select

from .agents.document_agent import build_document_graph
from .agents.email_agent import build_email_graph
from .config import settings
from .db import LLMCall, SessionLocal, Task, init_db, now
from .rag import get_kb

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Agents IA PME", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_methods=["*"], allow_headers=["*"])

email_graph = build_email_graph()
document_graph = build_document_graph()


def check_key(x_api_key: str = Header(default="")) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(401, "Clé API invalide")


def _save(kind: str, ref: str, category: str, status: str, output: dict, ms: int, calls: list) -> Task:
    with SessionLocal() as db:
        t = Task(kind=kind, source_ref=ref, category=category, status=status,
                 agent_output=output, processing_ms=ms)
        db.add(t)
        db.flush()
        db.add_all(LLMCall(task_id=t.id, **c) for c in calls)
        db.commit()
        return t


# ---------- Agents ----------

class EmailIn(BaseModel):
    sender: str = ""
    subject: str = ""
    body: str
    message_id: str = ""


@app.post("/agents/email", dependencies=[Depends(check_key)])
def run_email_agent(email: EmailIn) -> dict:
    t0 = time.perf_counter()
    s = email_graph.invoke({"sender": email.sender, "subject": email.subject, "body": email.body})
    ms = int((time.perf_counter() - t0) * 1000)
    output = {k: s.get(k) for k in ("category", "urgency", "references", "summary", "draft")}
    output["sources"] = [c["source"] for c in s.get("context", [])]
    output["email"] = email.model_dump()
    task = _save("email", email.message_id, s.get("category", ""), s.get("status", "echec"),
                 output, ms, s.get("llm_calls", []))
    return {"task_id": task.id, "status": task.status, **output}


class DocumentTextIn(BaseModel):
    filename: str = "document.txt"
    text: str


def _run_doc(state: dict, filename: str) -> dict:
    t0 = time.perf_counter()
    s = document_graph.invoke(state)
    ms = int((time.perf_counter() - t0) * 1000)
    output = {"fields": s.get("fields", {}), "errors": s.get("errors", []),
              "ocr_used": s.get("ocr_used", False), "filename": filename}
    task = _save("document", filename, s.get("fields", {}).get("document_type", ""),
                 s.get("status", "echec"), output, ms, s.get("llm_calls", []))
    return {"task_id": task.id, "status": task.status, **output}


@app.post("/agents/document", dependencies=[Depends(check_key)])
async def run_document_agent(file: UploadFile = File(...)) -> dict:
    return _run_doc({"filename": file.filename, "pdf_bytes": await file.read()}, file.filename)


@app.post("/agents/document/text", dependencies=[Depends(check_key)])
def run_document_agent_text(doc: DocumentTextIn) -> dict:
    return _run_doc({"filename": doc.filename, "text": doc.text}, doc.filename)


# ---------- Base de connaissances ----------

class KnowledgeIn(BaseModel):
    source: str
    text: str


@app.post("/kb", dependencies=[Depends(check_key)])
def add_knowledge(k: KnowledgeIn) -> dict:
    return {"chunks": get_kb().add(k.text, k.source)}


@app.get("/kb/search", dependencies=[Depends(check_key)])
def search_knowledge(q: str, k: int = 4) -> list[dict]:
    return get_kb().search(q, k)


# ---------- Validation humaine ----------

@app.get("/tasks", dependencies=[Depends(check_key)])
def list_tasks(status: str | None = "a_valider", limit: int = 50) -> list[dict]:
    with SessionLocal() as db:
        q = select(Task).order_by(Task.created_at.desc()).limit(limit)
        if status:
            q = q.where(Task.status == status)
        return [{"id": t.id, "kind": t.kind, "category": t.category, "status": t.status,
                 "agent_output": t.agent_output, "created_at": t.created_at.isoformat()}
                for t in db.scalars(q)]


class ReviewIn(BaseModel):
    decision: Literal["valide", "corrige", "rejete"]
    final_output: dict | None = None
    review_seconds: float


@app.post("/tasks/{task_id}/review", dependencies=[Depends(check_key)])
def review_task(task_id: int, r: ReviewIn) -> dict:
    with SessionLocal() as db:
        t = db.get(Task, task_id)
        if not t:
            raise HTTPException(404, "Tâche introuvable")
        t.status, t.final_output = r.decision, r.final_output or t.agent_output
        t.review_seconds, t.reviewed_at = r.review_seconds, now()
        db.commit()
        return {"id": t.id, "status": t.status}


# ---------- Indicateurs ----------

@app.get("/metrics", dependencies=[Depends(check_key)])
def metrics() -> dict:
    with SessionLocal() as db:
        by_status = dict(db.execute(select(Task.status, func.count()).group_by(Task.status)).all())
        total = sum(by_status.values())
        reviewed = sum(by_status.get(k, 0) for k in ("valide", "corrige", "rejete"))
        automated = total - by_status.get("echec", 0)
        cost_usd = db.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0)))
        tokens = db.execute(select(func.coalesce(func.sum(LLMCall.input_tokens), 0),
                                   func.coalesce(func.sum(LLMCall.output_tokens), 0))).one()
        saved_min = 0.0
        for kind, baseline in (("email", settings.baseline_min_email),
                               ("document", settings.baseline_min_document)):
            n, review_s = db.execute(
                select(func.count(), func.coalesce(func.sum(Task.review_seconds), 0.0))
                .where(Task.kind == kind, Task.status.in_(["valide", "corrige"]))).one()
            saved_min += n * baseline - review_s / 60
        # Un spam ignoré automatiquement économise aussi un tri manuel (~0,5 min)
        saved_min += by_status.get("ignore", 0) * 0.5

    cost_eur = cost_usd * settings.usd_to_eur
    saved_eur = saved_min / 60 * settings.hourly_cost_eur
    return {
        "tasks_total": total,
        "by_status": by_status,
        "automation_rate": round(automated / total, 3) if total else None,
        "error_rate": round((by_status.get("corrige", 0) + by_status.get("rejete", 0)) / reviewed, 3)
        if reviewed else None,
        "time_saved_hours": round(saved_min / 60, 2),
        "llm_cost_eur": round(cost_eur, 4),
        "cost_per_task_eur": round(cost_eur / total, 5) if total else None,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "net_gain_eur": round(saved_eur - cost_eur, 2),
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": settings.llm_provider}
