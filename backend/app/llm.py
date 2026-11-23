"""Couche fournisseur LLM : Gemini (principal), Claude ou OpenAI (comparaison), ou mock.

Chaque appel renvoie un LLMResult avec tokens, coût et latence : c'est la base
de tous les indicateurs de coût du projet.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from typing import Any

from .config import settings

# Prix en USD par million de tokens (entrée, sortie) — à mettre à jour si la grille change.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    # Gemini (tarifs Google AI Studio, prompts < 200k tokens — à vérifier sur ai.google.dev/pricing)
    # Ajouter ici les prix de gemini-3.5-flash et gemini-3.5-flash-lite (modèles par défaut) :
    # "gemini-3.5-flash": (entrée, sortie), "gemini-3.5-flash-lite": (entrée, sortie),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "mock-rules": (0.0, 0.0),
}


@dataclass
class LLMResult:
    task: str
    provider: str
    model: str
    data: Any
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    refused: bool = False

    def log(self) -> dict:
        d = asdict(self)
        d.pop("data")
        return d


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES_USD_PER_MTOK.get(model, (0.0, 0.0))
    return round(input_tokens * pin / 1e6 + output_tokens * pout / 1e6, 6)


class LLMProvider:
    name = "base"

    def complete(self, task: str, system: str, prompt: str, *, schema: dict | None = None,
                 model: str | None = None, effort: str = "medium") -> LLMResult:
        raise NotImplementedError


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self) -> None:
        import anthropic
        self.client = anthropic.Anthropic()  # lit ANTHROPIC_API_KEY ou le profil `ant auth login`

    def complete(self, task, system, prompt, *, schema=None, model=None, effort="medium"):
        model = model or settings.draft_model
        output_config: dict[str, Any] = {"effort": effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        t0 = time.perf_counter()
        resp = self.client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
            # Repli côté serveur si le modèle refuse la requête (classifieurs de sécurité)
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
        latency = int((time.perf_counter() - t0) * 1000)
        refused = resp.stop_reason == "refusal"
        text = "".join(b.text for b in resp.content if b.type == "text")
        data: Any = None
        if not refused:
            data = json.loads(text) if schema else text.strip()
        return LLMResult(task, self.name, model, data, resp.usage.input_tokens,
                         resp.usage.output_tokens,
                         cost_usd(model, resp.usage.input_tokens, resp.usage.output_tokens),
                         latency, refused)


class GeminiProvider(LLMProvider):
    """Fournisseur principal du projet : Google Gemini via le SDK google-genai."""
    name = "gemini"

    def __init__(self) -> None:
        from google import genai
        self.client = genai.Client()  # lit GEMINI_API_KEY (ou GOOGLE_API_KEY)

    def complete(self, task, system, prompt, *, schema=None, model=None, effort="medium"):
        from google.genai import types
        # Modèle léger pour classer/extraire, modèle principal pour rédiger
        model = settings.gemini_fast_model if effort == "low" else settings.gemini_model
        sys = system
        if schema:
            sys += ("\nRéponds uniquement avec un objet JSON conforme à ce schéma JSON, sans texte autour :\n"
                    + json.dumps(schema, ensure_ascii=False))
        config = types.GenerateContentConfig(
            system_instruction=sys,
            response_mime_type="application/json" if schema else "text/plain",
        )
        from google.genai import errors
        t0 = time.perf_counter()
        for attempt in range(5):  # 503 (surcharge) et 429 (quota) sont temporaires : on réessaie
            try:
                resp = self.client.models.generate_content(model=model, contents=prompt, config=config)
                break
            except errors.APIError as e:
                if e.code not in (429, 500, 503) or attempt == 4:
                    raise
                time.sleep(2 * 2 ** attempt)  # 2, 4, 8, 16 s
        latency = int((time.perf_counter() - t0) * 1000)
        text = resp.text or ""
        refused = not text.strip()  # réponse bloquée par les filtres de sécurité
        data: Any = None
        if not refused:
            data = json.loads(text) if schema else text.strip()
        u = resp.usage_metadata
        tin = (u.prompt_token_count or 0) if u else 0
        # Les tokens de réflexion (thinking) sont facturés comme des tokens de sortie
        tout = ((u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)) if u else 0
        return LLMResult(task, self.name, model, data, tin, tout, cost_usd(model, tin, tout), latency, refused)


class OpenAIProvider(LLMProvider):
    """Utilisé uniquement pour la comparaison Claude / GPT du chapitre d'évaluation."""
    name = "openai"

    def __init__(self) -> None:
        from openai import OpenAI
        self.client = OpenAI()

    def complete(self, task, system, prompt, *, schema=None, model=None, effort="medium"):
        model = settings.openai_model
        sys = system + (f"\nRéponds uniquement en JSON conforme à ce schéma : {json.dumps(schema)}"
                        if schema else "")
        t0 = time.perf_counter()
        resp = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": prompt}],
            response_format={"type": "json_object"} if schema else None,
        )
        latency = int((time.perf_counter() - t0) * 1000)
        text = resp.choices[0].message.content or ""
        data = json.loads(text) if schema else text.strip()
        u = resp.usage
        return LLMResult(task, self.name, model, data, u.prompt_tokens, u.completion_tokens,
                         cost_usd(model, u.prompt_tokens, u.completion_tokens), latency)


class MockProvider(LLMProvider):
    """Baseline par règles : fonctionne sans clé API (tests, démo, point de comparaison)."""
    name = "mock"

    KEYWORDS = {
        "spam": ["gagnez", "félicitations", "cliquez ici", "offre exclusive", "bitcoin", "promo"],
        "reclamation": ["réclamation", "mécontent", "inadmissible", "endommagé", "remboursement",
                        "toujours pas reçu", "retard", "défectueux", "erreur sur"],
        "devis": ["devis", "tarif", "prix", "proposition commerciale", "chiffrage", "cotation"],
        "facture": ["facture", "paiement", "règlement", "échéance", "avoir"],
        "support": ["bug", "erreur", "ne fonctionne", "connexion", "mot de passe", "bloqué",
                    "assistance", "problème technique"],
        "information": ["horaires", "renseignement", "information", "adresse", "savoir si"],
    }

    def complete(self, task, system, prompt, *, schema=None, model=None, effort="medium"):
        text = prompt.lower()
        if task == "classify_email":
            scores = {c: sum(k in text for k in kws) for c, kws in self.KEYWORDS.items()}
            category = max(scores, key=scores.get) if max(scores.values()) else "information"
            urgency = "haute" if any(k in text for k in ["urgent", "asap", "immédiatement"]) else "normale"
            refs = re.findall(r"\b(?:CMD|FAC|DEV)[-\s]?\d{3,}\b", prompt, re.I)
            data = {"category": category, "urgency": urgency, "references": refs,
                    "summary": prompt.strip().splitlines()[0][:120]}
        elif task == "extract_document":
            data = _regex_extract(prompt)
        elif task == "draft_reply":
            data = ("Bonjour,\n\nNous avons bien reçu votre message et revenons vers vous "
                    "dans les plus brefs délais.\n\nCordialement,\nL'équipe")
        else:
            data = "Réponse non disponible en mode mock."
        return LLMResult(task, self.name, "mock-rules", data)


def _num(s: str | None) -> float | None:
    if not s:
        return None
    return float(s.replace(" ", "").replace(" ", "").replace(",", "."))


def _regex_extract(text: str) -> dict:
    def find(pattern: str) -> str | None:
        m = re.search(pattern, text, re.I)
        return m.group(1).strip() if m else None

    date = find(r"date[^:\n]*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})")
    if date and "/" in date:
        d, m, y = date.split("/")
        date = f"{y}-{m}-{d}"
    amount = r"([\d\s ]+[,.]\d{2})"
    return {
        "document_type": "facture" if re.search(r"facture", text, re.I) else
                         "devis" if re.search(r"devis", text, re.I) else "autre",
        "supplier": next((l.strip() for l in text.splitlines() if l.strip()), ""),
        "document_number": find(r"(?:facture|devis|n°|numéro)\s*(?:n°|no|numéro)?\s*:?\s*([A-Z]{2,4}[-/]?\d[\w-]*)") or "",
        "date": date or "",
        "total_ht": _num(find(r"total\s*HT\s*:?\s*" + amount)),
        "tva": _num(find(r"TVA[^:\n]*:\s*" + amount)),
        "total_ttc": _num(find(r"total\s*TTC\s*:?\s*" + amount)),
        "currency": "EUR",
    }


def get_provider(name: str | None = None) -> LLMProvider:
    name = name or settings.llm_provider
    if name == "gemini":
        return GeminiProvider()
    if name == "claude":
        return ClaudeProvider()
    if name == "openai":
        return OpenAIProvider()
    return MockProvider()
