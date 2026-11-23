"""Agent Email (LangGraph) : masquer -> classer -> chercher (RAG) -> rédiger un brouillon.

Le brouillon n'est jamais envoyé : il part dans la file de validation humaine.
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..llm import LLMProvider, get_provider
from ..privacy import mask, unmask
from ..rag import get_kb

CATEGORIES = ["devis", "facture", "reclamation", "support", "information", "spam"]

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "urgency": {"type": "string", "enum": ["basse", "normale", "haute"]},
        "references": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["category", "urgency", "references", "summary"],
    "additionalProperties": False,
}

CLASSIFY_SYSTEM = """Tu tries les emails entrants d'une PME française.
Catégories :
- devis : demande de prix, de devis ou de proposition commerciale
- facture : question sur une facture, un paiement, un avoir, une relance de paiement
- reclamation : client mécontent, retard de livraison, produit défectueux, demande de remboursement
- support : problème technique, accès, bug, aide à l'utilisation
- information : question générale (horaires, adresse, disponibilité, documentation)
- spam : publicité non sollicitée, hameçonnage, message sans rapport avec l'activité
Si un email mélange plusieurs sujets, choisis celui qui demande une action de l'entreprise.
Urgence haute uniquement si le client l'exprime ou si un délai court est en jeu.
references : numéros de commande, de facture ou de devis cités. summary : une phrase."""

DRAFT_SYSTEM = """Tu rédiges des brouillons de réponse pour une PME française, relus par un collaborateur.
Règles : vouvoiement, ton professionnel et chaleureux, 120 mots maximum.
N'utilise que les faits présents dans l'email et dans les extraits de la base de connaissances.
Si une information manque (prix, délai, statut), ne l'invente pas : écris [À COMPLÉTER : ...].
Les jetons comme [EMAIL_1] ou [TEL_2] remplacent des données personnelles : recopie-les tels quels.
Termine par « Cordialement, » suivi de [SIGNATURE]."""


class EmailState(TypedDict, total=False):
    sender: str
    subject: str
    body: str
    masked: str
    mapping: dict
    category: str
    urgency: str
    references: list
    summary: str
    context: list
    draft: str
    status: str  # "a_valider" | "ignore" | "echec"
    llm_calls: list


def build_email_graph(provider: LLMProvider | None = None):
    llm = provider or get_provider()

    def mask_node(s: EmailState) -> EmailState:
        text, mapping = mask(f"De : {s.get('sender', '')}\nObjet : {s.get('subject', '')}\n\n{s.get('body', '')}")
        return {"masked": text, "mapping": mapping, "llm_calls": []}

    def classify_node(s: EmailState) -> EmailState:
        r = llm.complete("classify_email", CLASSIFY_SYSTEM, s["masked"], schema=CLASSIFY_SCHEMA,
                         model=settings.classify_model, effort="low")
        calls = s["llm_calls"] + [r.log()]
        if r.refused:
            return {"status": "echec", "llm_calls": calls}
        return {**r.data, "llm_calls": calls}

    def route(s: EmailState) -> str:
        if s.get("status") == "echec":
            return "end"
        return "end_spam" if s["category"] == "spam" else "retrieve"

    def spam_node(s: EmailState) -> EmailState:
        return {"status": "ignore"}

    def retrieve_node(s: EmailState) -> EmailState:
        return {"context": get_kb().search(f"{s.get('subject', '')} {s.get('summary', '')}", k=4)}

    def draft_node(s: EmailState) -> EmailState:
        ctx = "\n\n".join(f"[{c['source']}] {c['text']}" for c in s.get("context", [])) or "(aucun extrait)"
        prompt = (f"Catégorie : {s['category']} — urgence : {s['urgency']}\n\n"
                  f"EMAIL :\n{s['masked']}\n\nEXTRAITS DE LA BASE DE CONNAISSANCES :\n{ctx}")
        r = llm.complete("draft_reply", DRAFT_SYSTEM, prompt, model=settings.draft_model, effort="medium")
        calls = s["llm_calls"] + [r.log()]
        if r.refused:
            return {"status": "echec", "llm_calls": calls}
        return {"draft": unmask(r.data, s["mapping"]), "status": "a_valider", "llm_calls": calls}

    g = StateGraph(EmailState)
    g.add_node("mask", mask_node)
    g.add_node("classify", classify_node)
    g.add_node("spam", spam_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("draft", draft_node)
    g.add_edge(START, "mask")
    g.add_edge("mask", "classify")
    g.add_conditional_edges("classify", route, {"end": END, "end_spam": "spam", "retrieve": "retrieve"})
    g.add_edge("spam", END)
    g.add_edge("retrieve", "draft")
    g.add_edge("draft", END)
    return g.compile()
