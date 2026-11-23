-- Vues PostgreSQL à connecter dans Power BI (Obtenir les données > PostgreSQL).
-- Paramètres de référence : à aligner sur la mesure de référence (BASELINE_MIN_*).

CREATE OR REPLACE VIEW v_taches_jour AS
SELECT date_trunc('day', created_at)::date AS jour,
       kind AS type_tache,
       category AS categorie,
       count(*) AS nb_taches,
       count(*) FILTER (WHERE status IN ('valide', 'corrige', 'rejete')) AS nb_relues,
       count(*) FILTER (WHERE status = 'valide') AS nb_valides,
       count(*) FILTER (WHERE status = 'corrige') AS nb_corrigees,
       count(*) FILTER (WHERE status = 'rejete') AS nb_rejetees,
       count(*) FILTER (WHERE status = 'echec') AS nb_echecs,
       avg(review_seconds) / 60.0 AS relecture_moy_min,
       avg(processing_ms) / 1000.0 AS traitement_moy_s
FROM tasks
GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW v_cout_jour AS
SELECT date_trunc('day', created_at)::date AS jour,
       model AS modele,
       task AS etape,
       count(*) AS nb_appels,
       sum(input_tokens) AS tokens_entree,
       sum(output_tokens) AS tokens_sortie,
       sum(cost_usd) * 0.92 AS cout_eur,
       avg(latency_ms) AS latence_moy_ms
FROM llm_calls
GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW v_gain_temps AS
SELECT date_trunc('week', created_at)::date AS semaine,
       kind AS type_tache,
       count(*) FILTER (WHERE status IN ('valide', 'corrige')) AS nb_traitees,
       sum(CASE WHEN status IN ('valide', 'corrige')
                THEN (CASE kind WHEN 'email' THEN 6.0 ELSE 9.0 END) - coalesce(review_seconds, 0) / 60.0
                ELSE 0 END) / 60.0 AS heures_gagnees
FROM tasks
GROUP BY 1, 2;
