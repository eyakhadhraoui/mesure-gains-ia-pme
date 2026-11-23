"""Configuration centralisée, lue depuis les variables d'environnement."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Charge le fichier .env à la racine du projet (sans écraser les variables déjà définies)."""
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.split(" #")[0].strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    # Fournisseur LLM : "gemini", "claude", "openai" ou "mock" (règles, sans clé API)
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock")
    # Gemini : modèle principal (rédaction) et modèle rapide (classification, extraction)
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    gemini_fast_model: str = os.getenv("GEMINI_FAST_MODEL", "gemini-3.5-flash-lite")
    # Modèles Claude (configurables pour comparer qualité / coût)
    classify_model: str = os.getenv("CLASSIFY_MODEL", "claude-opus-5-5")
    draft_model: str = os.getenv("DRAFT_MODEL", "claude-opus-5-5")
    extract_model: str = os.getenv("EXTRACT_MODEL", "claude-opus-5-5")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./agents.db")
    chroma_path: str = os.getenv("CHROMA_PATH", "./chroma_data")
    api_key: str = os.getenv("API_KEY", "")  # vide = pas d'authentification (dev)
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv("CORS_ORIGINS", "http://localhost:4200").split(",")
    )

    # Mesure de référence : minutes de traitement manuel par tâche (chronométrage)
    baseline_min_email: float = _float("BASELINE_MIN_EMAIL", 6.0)
    baseline_min_document: float = _float("BASELINE_MIN_DOCUMENT", 9.0)
    hourly_cost_eur: float = _float("HOURLY_COST_EUR", 35.0)
    usd_to_eur: float = _float("USD_TO_EUR", 0.92)


settings = Settings()
