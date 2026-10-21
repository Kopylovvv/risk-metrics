-- Что лежит в БД после расчёта (для показа). Запуск из корня репозитория:
-- docker compose exec -T db psql -U risk_metrics -d risk_metrics < backend/scripts/show_db.sql

\pset border 2
\pset null '∅'

\echo '1. Наборы сделок и их revision'
SELECT id, revision, created_at, updated_at
FROM workspaces ORDER BY updated_at DESC LIMIT 5;

\echo '2. Сделки последнего изменённого набора'
SELECT t.isin, t.quantity, i.name AS instrument, t.updated_at
FROM trades t
LEFT JOIN instruments i ON i.id = t.instrument_id
WHERE t.workspace_id = (SELECT id FROM workspaces ORDER BY updated_at DESC LIMIT 1)
ORDER BY t.created_at;

\echo '3. Рыночные данные: последний снимок по каждой бумаге и курсы'
SELECT DISTINCT ON (i.isin) i.isin, s.provider, s.data_as_of, s.currency, s.nominal,
       s.price_pct, s.duration_mod, s.convexity, s.coupon_type, s.coupon_frequency
FROM market_data_snapshots s JOIN instruments i ON i.id = s.instrument_id
ORDER BY i.isin, s.data_as_of DESC;
SELECT currency, rate_date, rate_to_rub, source FROM fx_rates ORDER BY rate_date DESC;

\echo '4. Последние расчёты'
SELECT id, workspace_revision, valuation_date, measure_code, methodology_version, status,
       total_value, completed_at
FROM calculation_runs ORDER BY created_at DESC LIMIT 5;

\echo '5. Строки последнего расчёта: входы на момент расчёта, параметры и DV01'
SELECT r.input_isin AS isin, r.input_quantity AS qty, r.status, r.error_code,
       r.nominal_used AS nominal, r.price_pct_used AS price, r.fx_rate_used AS fx,
       round(r.current_fair_value, 2) AS cfv_rub, r.duration_used AS d, r.convexity_used AS c,
       round(r.metric_value, 4) AS dv01_rub, s.provider, s.data_as_of,
       r.trade_id IS NULL AS trade_deleted
FROM calculation_results r
LEFT JOIN market_data_snapshots s ON s.id = r.market_data_snapshot_id
WHERE r.run_id = (SELECT id FROM calculation_runs ORDER BY created_at DESC LIMIT 1)
ORDER BY r.input_isin;
