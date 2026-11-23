"""Agent Documents (LangGraph) : lire (texte ou OCR) -> extraire en JSON -> valider -> indexer."""
from __future__ import annotations

import io
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..llm import LLMProvider, get_provider
from ..privacy import mask
from ..rag import get_kb

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": ["facture", "devis", "bon_de_commande", "autre"]},
        "supplier": {"type": "string"},
        "document_number": {"type": "string"},
        "date": {"type": "string", "description": "AAAA-MM-JJ"},
        "total_ht": {"type": ["number", "null"]},
        "tva": {"type": ["number", "null"]},
        "total_ttc": {"type": ["number", "null"]},
        "currency": {"type": "string"},
    },
    "required": ["document_type", "supplier", "document_number", "date",
                 "total_ht", "tva", "total_ttc", "currency"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM = """Tu extrais les champs d'un document commercial français (facture, devis, bon de commande).
supplier = l'entreprise émettrice du document, pas le client.
Dates au format AAAA-MM-JJ. Montants en nombres décimaux (1 234,50 € -> 1234.5).
Si un champ est absent du document, renvoie une chaîne vide ou null : n'invente jamais."""


def read_pdf(data: bytes) -> tuple[str, bool]:
    """Texte natif via pypdf ; si la page est scannée (pas de texte), OCR Tesseract."""
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    if len(text.strip()) > 30:
        return text, False
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        pages = convert_from_bytes(data, dpi=300)
        return "\n".join(pytesseract.image_to_string(p, lang="fra") for p in pages), True
    except ImportError as e:
        raise RuntimeError("PDF scanné : installer pytesseract, pdf2image et Tesseract (fra)") from e


class DocState(TypedDict, total=False):
    filename: str
    pdf_bytes: bytes
    text: str
    ocr_used: bool
    fields: dict
    errors: list
    status: str
    llm_calls: list


def validate_fields(f: dict) -> list[str]:
    errors = []
    for key in ("supplier", "document_number", "date"):
        if not f.get(key):
            errors.append(f"champ manquant : {key}")
    ht, tva, ttc = f.get("total_ht"), f.get("tva"), f.get("total_ttc")
    if None not in (ht, tva, ttc) and abs(ht + tva - ttc) > 0.02:
        errors.append(f"incohérence montants : {ht} + {tva} != {ttc}")
    if f.get("date") and not (len(f["date"]) == 10 and f["date"][4] == "-"):
        errors.append("date non normalisée")
    return errors


def build_document_graph(provider: LLMProvider | None = None, index: bool = True):
    llm = provider or get_provider()

    def read_node(s: DocState) -> DocState:
        if s.get("text"):
            return {"ocr_used": False, "llm_calls": []}
        text, ocr = read_pdf(s["pdf_bytes"])
        return {"text": text, "ocr_used": ocr, "llm_calls": []}

    def extract_node(s: DocState) -> DocState:
        masked, _ = mask(s["text"])
        r = llm.complete("extract_document", EXTRACT_SYSTEM, masked, schema=EXTRACT_SCHEMA,
                         model=settings.extract_model, effort="low")
        calls = s["llm_calls"] + [r.log()]
        if r.refused:
            return {"status": "echec", "fields": {}, "llm_calls": calls}
        return {"fields": r.data, "llm_calls": calls}

    def validate_node(s: DocState) -> DocState:
        if s.get("status") == "echec":
            return {}
        return {"errors": validate_fields(s["fields"]), "status": "a_valider"}

    def index_node(s: DocState) -> DocState:
        if index and s.get("status") == "a_valider":
            f = s["fields"]
            get_kb().add(s["text"], source=s.get("filename", "document"),
                         metadata={"type": f.get("document_type", ""), "supplier": f.get("supplier", "")})
        return {}

    g = StateGraph(DocState)
    g.add_node("read", read_node)
    g.add_node("extract", extract_node)
    g.add_node("validate", validate_node)
    g.add_node("index", index_node)
    g.add_edge(START, "read")
    g.add_edge("read", "extract")
    g.add_edge("extract", "validate")
    g.add_edge("validate", "index")
    g.add_edge("index", END)
    return g.compile()
