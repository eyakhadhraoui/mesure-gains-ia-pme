# Mesurer les gains de l'IA dans une PME

**Comment savoir, chiffres à l'appui, ce que des agents IA rapportent à une PME de services ?**

Ce projet teste une méthode de mesure sur deux agents pilotes, **Email** et **Documents**, orchestrés par n8n et toujours validés par un humain. Chaque tâche est mesurée : temps gagné, erreurs, coût, ROI.

**Premiers résultats** (jeu de test synthétique de 40 emails et 10 factures) :

| Approche | Classification des emails | Extraction des factures |
| --- | --- | --- |
| Règles, sans IA | 77,5 % | 97,1 % |
| Gemini 3.5 Flash-Lite | 97,5 % | 100 % |

```
Emails / PDF / demandes ──► n8n ──► API FastAPI ──► Agent Email (LangGraph)      ──► LLM (Claude ou GPT)
                                                └► Agent Documents (LangGraph)  ──► ChromaDB (RAG)
                                                         │
                                PostgreSQL (tâches, appels LLM, décisions) ──► Angular (validation) / Power BI (ROI)
```

## Arborescence

| Dossier | Contenu |
| --- | --- |
| `backend/app/agents/` | `email_agent.py` (masquer → classer → RAG → brouillon), `document_agent.py` (lecture/OCR → extraction JSON → contrôles → indexation) |
| `backend/app/llm.py` | Fournisseurs `gemini` (principal, SDK `google-genai`), `claude` et `openai` (comparaison), `mock` (règles, sans clé) + calcul du coût par appel |
| `backend/app/privacy.py` | Masquage RGPD (emails, téléphones, IBAN) avant envoi au LLM |
| `backend/app/main.py` | API : `/agents/email`, `/agents/document`, `/kb`, `/tasks`, `/tasks/{id}/review`, `/metrics` |
| `backend/tests/` | Tests de bout en bout en mode mock |
| `evaluation/` | Jeu de test étiqueté (40 emails, 10 documents), `evaluate.py`, `kpi.py` (ROI) |
| `n8n/workflows/` | Workflows à importer : email IMAP → agent, PDF déposé → agent |
| `frontend/` | Angular 18 : file de validation (chronomètre la relecture) + indicateurs |
| `powerbi/vues_kpi.sql` | Vues PostgreSQL pour le rapport Power BI |

## Démarrage rapide (local, sans clé API)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate sous Linux/macOS)
pip install -r backend/requirements.txt

cd backend
pytest -q                          # tests en mode mock
uvicorn app.main:app --reload      # http://localhost:8000/docs
```

Interface Angular :

```bash
cd frontend
npm install
npm start                          # http://localhost:4200
```

## Avec Gemini (fournisseur principal)

1. Créer une clé gratuite sur https://aistudio.google.com/apikey
2. Configurer et lancer :

```bash
copy .env.example .env             # puis LLM_PROVIDER=gemini et GEMINI_API_KEY=...
                                   # (le backend lit .env automatiquement, aussi hors Docker)
docker compose up --build          # API :8000, n8n :5678, PostgreSQL :5432
```

Choix techniques côté Gemini :
- `gemini-3.5-flash-lite` (`GEMINI_FAST_MODEL`) pour classer et extraire : rapide et peu cher ;
- `gemini-3.5-flash` (`GEMINI_MODEL`) pour rédiger les brouillons ;
- nouvelles tentatives automatiques en cas de surcharge (503) ou de quota (429) ;
- ajouter les prix de ces modèles dans `PRICES_USD_PER_MTOK` (`backend/app/llm.py`), sinon le coût n'est pas compté ;
- réponses forcées en JSON (`response_mime_type="application/json"` + schéma dans les instructions) ;
- une réponse bloquée par les filtres de sécurité fait passer la tâche en `echec`, donc en traitement manuel ;
- chaque appel est journalisé : tokens (y compris les tokens de réflexion), coût en USD, latence (table `llm_calls`).

Claude (`LLM_PROVIDER=claude`) et GPT (`LLM_PROVIDER=openai`) restent disponibles pour la comparaison du chapitre d'évaluation.

## Évaluation

```bash
python evaluation/evaluate.py --provider mock        # baseline par règles (point de comparaison)
python evaluation/evaluate.py --provider gemini      # ~50 appels, quelques centimes
python evaluation/evaluate.py --provider claude      # comparaison (optionnel)
python evaluation/evaluate.py --provider openai      # comparaison (optionnel)
python evaluation/seed_demo.py                       # remplit la file de validation (API lancée)
python evaluation/kpi.py --emails-month 600 --docs-month 250 --project-cost-eur 6000
python evaluation/kpi.py --from-api http://localhost:8000   # avec les chiffres réels du pilote
```

Les rapports JSON sont écrits dans `evaluation/results/`.

> Les données de `evaluation/data/` sont **synthétiques**, y compris `baseline_times.csv`, qui n'est qu'un exemple de format. Remplacez-les par le chronométrage réel et par des emails et documents anonymisés de la PME pilote avant de citer des chiffres dans le mémoire.

## n8n

1. Ouvrir http://localhost:5678, puis *Import from file* et choisir les fichiers de `n8n/workflows/`.
2. Renseigner les identifiants IMAP et SMTP.
3. Les nœuds HTTP lisent `API_URL` et `API_KEY` dans les variables d'environnement du conteneur.

## Power BI

Exécuter `powerbi/vues_kpi.sql` sur la base, puis dans Power BI : *Obtenir les données*, *PostgreSQL*, `localhost:5432/agents`, et choisir les vues `v_taches_jour`, `v_cout_jour` et `v_gain_temps`.
