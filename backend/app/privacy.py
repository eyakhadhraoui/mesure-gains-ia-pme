"""Masquage des données personnelles avant tout envoi à un LLM externe (RGPD)."""
import re

_PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("IBAN", re.compile(r"\bFR\d{2}(?:\s?[A-Z0-9]{4}){5}\s?[A-Z0-9]{3}\b", re.I)),
    ("TEL", re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}")),
]


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Remplace les données sensibles par des jetons et renvoie la table de correspondance."""
    mapping: dict[str, str] = {}
    for label, pattern in _PATTERNS:
        def _sub(m: re.Match, label=label) -> str:
            token = f"[{label}_{len(mapping) + 1}]"
            mapping[token] = m.group(0)
            return token
        text = pattern.sub(_sub, text)
    return text, mapping


def unmask(text: str, mapping: dict[str, str]) -> str:
    for token, value in mapping.items():
        text = text.replace(token, value)
    return text
