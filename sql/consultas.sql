-- ============================================================
-- Mini caso: El costo invisible del no-show
-- Consultas SQL del análisis (SQLite / sintaxis portable)
-- Tabla: reservas (datos sintéticos, ver datos/reservas_sinteticas.csv)
-- ============================================================

-- 1) Tasa general de no-show
SELECT
    COUNT(*)                                              AS reservas,
    SUM(estado = 'no_show')                               AS no_shows,
    ROUND(100.0 * SUM(estado = 'no_show') / COUNT(*), 1)  AS tasa_no_show_pct
FROM reservas;

-- 2) No-show por canal de reserva  → ¿dónde se concentra el problema?
SELECT
    canal,
    COUNT(*)                                              AS reservas,
    ROUND(100.0 * SUM(estado = 'no_show') / COUNT(*), 1)  AS tasa_no_show_pct
FROM reservas
GROUP BY canal
ORDER BY tasa_no_show_pct DESC;

-- 3) No-show por tipo de vehículo
SELECT
    tipo_vehiculo,
    COUNT(*)                                              AS reservas,
    ROUND(100.0 * SUM(estado = 'no_show') / COUNT(*), 1)  AS tasa_no_show_pct
FROM reservas
GROUP BY tipo_vehiculo
ORDER BY tasa_no_show_pct DESC;

-- 4) No-show según días de anticipación (lead time)
--    Hipótesis: quien reserva con mucha anticipación, olvida o cambia planes.
SELECT
    CASE WHEN lead_time_dias <= 2  THEN '0-2 días'
         WHEN lead_time_dias <= 7  THEN '3-7 días'
         WHEN lead_time_dias <= 14 THEN '8-14 días'
         WHEN lead_time_dias <= 30 THEN '15-30 días'
         ELSE '31+ días' END                              AS tramo,
    COUNT(*)                                              AS reservas,
    ROUND(100.0 * SUM(estado = 'no_show') / COUNT(*), 1)  AS tasa_no_show_pct
FROM reservas
GROUP BY tramo
ORDER BY MIN(lead_time_dias);

-- 5) Ingreso en riesgo por no-show al mes (ticket = precio_día × días de renta)
SELECT
    COUNT(*)                                                       AS no_shows,
    ROUND(SUM(precio_dia_usd * dias_renta), 0)                     AS ingreso_perdido_usd
FROM reservas
WHERE estado = 'no_show';

-- 6) Resultado del experimento A/B (recordatorio 48 h antes)
SELECT
    grupo_ab,
    COUNT(*)                                              AS n,
    SUM(estado = 'no_show')                               AS no_shows,
    ROUND(100.0 * SUM(estado = 'no_show') / COUNT(*), 2)  AS tasa_no_show_pct
FROM reservas
WHERE grupo_ab IN ('A_control', 'B_recordatorio')
GROUP BY grupo_ab;
