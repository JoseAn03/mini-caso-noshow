"""
Mini caso: El costo invisible del no-show
=========================================
Caso práctico de análisis de datos con datos 100% sintéticos que replican
la estructura de un problema real de alquiler de vehículos.

Pipeline:
  1) Generación de datos sintéticos (seed fija, reproducible)
  2) Consultas SQL (SQLite) para segmentar el problema
  3) Experimento A/B: recordatorio 48 h antes vs. sin recordatorio
  4) Visual tipo dashboard

Autor: José Andrés Sequeira Hernández
"""
import json
import math
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
rng = np.random.default_rng(42)

# ----------------------------------------------------------------------
# 1) DATOS SINTÉTICOS — 6.000 reservas en 90 días
# ----------------------------------------------------------------------
N = 6000
fechas_retiro = pd.Timestamp("2026-07-01") + pd.to_timedelta(rng.integers(0, 90, N), unit="D")
lead_time = np.clip(rng.gamma(shape=2.0, scale=6.0, size=N).astype(int), 0, 60)  # días de anticipación
canal = rng.choice(["Web", "Mostrador", "Teléfono", "Agencia"], N, p=[0.52, 0.18, 0.12, 0.18])
vehiculo = rng.choice(["Económico", "Compacto", "SUV", "Pickup", "Premium"], N, p=[0.34, 0.28, 0.22, 0.08, 0.08])
tarifa = {"Económico": 38, "Compacto": 45, "SUV": 68, "Pickup": 74, "Premium": 110}
precio_dia = np.array([tarifa[v] for v in vehiculo]) * rng.uniform(0.85, 1.3, N)
dias_renta = rng.integers(1, 12, N)
frecuente = rng.random(N) < 0.28

# Probabilidad base de no-show: crece con el lead time y en canal Web
p_base = 0.085 + 0.0038 * lead_time + np.where(canal == "Web", 0.045, 0.0) \
         + np.where(canal == "Agencia", 0.02, 0.0) - np.where(frecuente, 0.03, 0.0)
p_base = np.clip(p_base, 0.02, 0.45)

# Experimento A/B en los últimos 45 días: la mitad recibe recordatorio 48 h antes
en_experimento = fechas_retiro >= pd.Timestamp("2026-08-15")
grupo = np.where(en_experimento, rng.choice(["A_control", "B_recordatorio"], N), "fuera")
odds = p_base / (1 - p_base)
odds = np.where(grupo == "B_recordatorio", odds * 0.70, odds)  # efecto real del recordatorio
p_final = odds / (1 + odds)
no_show = rng.random(N) < p_final

df = pd.DataFrame({
    "id_reserva": [f"R-{10000 + i}" for i in range(N)],
    "fecha_reserva": fechas_retiro - pd.to_timedelta(lead_time, unit="D"),
    "fecha_retiro": fechas_retiro,
    "lead_time_dias": lead_time,
    "canal": canal,
    "tipo_vehiculo": vehiculo,
    "precio_dia_usd": precio_dia.round(2),
    "dias_renta": dias_renta,
    "cliente_frecuente": frecuente,
    "grupo_ab": grupo,
    "estado": np.where(no_show, "no_show", "completada"),
})
df.to_csv(BASE / "datos" / "reservas_sinteticas.csv", index=False)

# ----------------------------------------------------------------------
# 2) SQL — el problema, segmentado (mismas consultas de consultas.sql)
# ----------------------------------------------------------------------
con = sqlite3.connect(":memory:")
df_sql = df.copy()
df_sql["fecha_retiro"] = df_sql["fecha_retiro"].astype(str)
df_sql.to_sql("reservas", con, index=False)

q_canal = pd.read_sql("""
    SELECT canal, COUNT(*) AS reservas,
           ROUND(100.0 * SUM(estado = 'no_show') / COUNT(*), 1) AS tasa_no_show_pct
    FROM reservas GROUP BY canal ORDER BY tasa_no_show_pct DESC
""", con)

q_lead = pd.read_sql("""
    SELECT CASE WHEN lead_time_dias <= 2  THEN '0-2 días'
                WHEN lead_time_dias <= 7  THEN '3-7 días'
                WHEN lead_time_dias <= 14 THEN '8-14 días'
                WHEN lead_time_dias <= 30 THEN '15-30 días'
                ELSE '31+ días' END AS tramo,
           COUNT(*) AS reservas,
           ROUND(100.0 * SUM(estado = 'no_show') / COUNT(*), 1) AS tasa_no_show_pct
    FROM reservas GROUP BY tramo ORDER BY MIN(lead_time_dias)
""", con)

q_ab = pd.read_sql("""
    SELECT grupo_ab, COUNT(*) AS n,
           SUM(estado = 'no_show') AS no_shows,
           ROUND(100.0 * SUM(estado = 'no_show') / COUNT(*), 2) AS tasa_pct
    FROM reservas WHERE grupo_ab IN ('A_control', 'B_recordatorio')
    GROUP BY grupo_ab
""", con)
con.close()

# ----------------------------------------------------------------------
# 3) TEST A/B — test z de dos proporciones
# ----------------------------------------------------------------------
a = q_ab[q_ab.grupo_ab == "A_control"].iloc[0]
b = q_ab[q_ab.grupo_ab == "B_recordatorio"].iloc[0]
n1, x1 = int(a.n), int(a.no_shows)
n2, x2 = int(b.n), int(b.no_shows)
p1, p2 = x1 / n1, x2 / n2
p_pool = (x1 + x2) / (n1 + n2)
se_pool = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
z = (p2 - p1) / se_pool
p_valor = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
se_dif = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
dif = p2 - p1
ic95 = (dif - 1.96 * se_dif, dif + 1.96 * se_dif)

tasa_general = 100.0 * (df.estado == "no_show").mean()
ticket_prom = float((df.precio_dia_usd * df.dias_renta).mean())
noshow_exp = n1 + n2
recuperadas = (p1 - p2) * noshow_exp / 2  # reservas salvadas aprox. en el grupo B
ingreso_rec = recuperadas * ticket_prom

res = {
    "reservas_totales": N,
    "tasa_no_show_general_pct": round(tasa_general, 1),
    "experimento": {
        "n_control": n1, "n_tratamiento": n2,
        "tasa_control_pct": round(100 * p1, 2),
        "tasa_recordatorio_pct": round(100 * p2, 2),
        "diferencia_pp": round(100 * dif, 2),
        "reduccion_relativa_pct": round(100 * (p1 - p2) / p1, 1),
        "z": round(z, 2), "p_valor": round(p_valor, 4),
        "ic95_pp": [round(100 * ic95[0], 2), round(100 * ic95[1], 2)],
    },
    "impacto": {
        "ticket_promedio_usd": round(ticket_prom, 0),
        "reservas_recuperadas_experimento": round(recuperadas, 0),
        "ingreso_recuperado_experimento_usd": round(ingreso_rec, 0),
    },
    "por_canal": q_canal.to_dict("records"),
    "tendencia_semanal": [
        {"semana": str(idx.date()), "tasa_no_show_pct": round(100.0 * val, 1)}
        for idx, val in df.set_index("fecha_retiro").resample("W")["estado"]
        .apply(lambda s: (s == "no_show").mean()).items()
    ],
    "por_lead_time": q_lead.to_dict("records"),
}
(BASE / "resultados.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(res, indent=2, ensure_ascii=False))

# ----------------------------------------------------------------------
# 4) VISUAL — dashboard estilo Power BI (tema oscuro)
# ----------------------------------------------------------------------
plt.rcParams.update({
    "figure.facecolor": "#0b1220", "axes.facecolor": "#0b1220",
    "savefig.facecolor": "#0b1220", "text.color": "#e8eef7",
    "axes.edgecolor": "#33507a", "axes.labelcolor": "#e8eef7",
    "xtick.color": "#aebfd6", "ytick.color": "#aebfd6", "font.size": 11,
})
CIAN, DORADO, ROJO, MORADO = "#22d3ee", "#fbbf24", "#f87171", "#818cf8"
sem = df.set_index("fecha_retiro").resample("W")["estado"].apply(lambda s: (s == "no_show").mean() * 100)

fig = plt.figure(figsize=(13.33, 8.6))
gs = fig.add_gridspec(3, 4, height_ratios=[0.85, 1.25, 1.0], hspace=0.62, wspace=0.34,
                      left=0.065, right=0.975, top=0.875, bottom=0.075)
fig.suptitle("EL COSTO INVISIBLE DEL NO-SHOW  ·  Mini caso SQL + Power BI + Test A/B",
             fontsize=16.5, fontweight="bold", x=0.065, ha="left")
fig.text(0.065, 0.912, "Datos sintéticos de una operación de alquiler de vehículos · 6.000 reservas · jul–sep 2026",
         fontsize=11.5, color="#8fb3dd")

kpis = [
    ("TASA DE NO-SHOW", f"{tasa_general:.1f}%", "de las reservas no se presentan", CIAN),
    ("PEOR CANAL: WEB", f"{q_canal.tasa_no_show_pct.max():.1f}%", "el teléfono es el mejor (8,9%)", "#38bdf8"),
    ("EFECTO DEL RECORDATORIO", f"−{100*(p1-p2)/p1:.0f}%", "de no-show en el test A/B", DORADO),
    ("INGRESO RECUPERADO", f"${ingreso_rec:,.0f}", "en 45 días de experimento", "#4ade80"),
]
for i, (titulo, valor, nota, color) in enumerate(kpis):
    ax = fig.add_subplot(gs[0, i]); ax.axis("off")
    ax.add_patch(plt.Rectangle((0.02, 0.05), 0.96, 0.9, facecolor="#101c33",
                               edgecolor=color, linewidth=1.4, zorder=1))
    ax.text(0.5, 0.79, titulo, ha="center", fontsize=10.5, color="#8fb3dd", zorder=2)
    ax.text(0.5, 0.48, valor, ha="center", fontsize=25, fontweight="bold", color=color, zorder=2)
    ax.text(0.5, 0.17, nota, ha="center", fontsize=9, color="#c6d4e8", zorder=2)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)

ax = fig.add_subplot(gs[1, 0:1])
ax.barh(q_canal.canal[::-1], q_canal.tasa_no_show_pct[::-1], color=[CIAN, "#38bdf8", MORADO, "#64748b"], height=0.62)
ax.set_title("No-show por canal (%)", fontsize=12, color="#e8eef7", loc="left")
ax.set_xlim(0, max(q_canal.tasa_no_show_pct) * 1.28)
for v, y in zip(q_canal.tasa_no_show_pct[::-1], range(len(q_canal))):
    ax.text(v + 0.4, y, f"{v:.1f}%", va="center", fontsize=10.5)
ax.grid(axis="x", color="#1d2f4d"); ax.set_axisbelow(True)

ax = fig.add_subplot(gs[1, 1:3])
ax.bar(q_lead.tramo, q_lead.tasa_no_show_pct, color=MORADO, width=0.6)
ax.set_title("No-show según días de anticipación (%)  ·  la pista de oro", fontsize=12, color="#e8eef7", loc="left")
ax.set_ylim(0, max(q_lead.tasa_no_show_pct) * 1.3)
for v, x in zip(q_lead.tasa_no_show_pct, range(len(q_lead))):
    ax.text(x, v + 0.5, f"{v:.1f}%", ha="center", fontsize=10.5)
ax.grid(axis="y", color="#1d2f4d"); ax.set_axisbelow(True)

ax = fig.add_subplot(gs[1, 3:4])
vals = [100 * p1, 100 * p2]
errs = [100 * 1.96 * math.sqrt(p1 * (1 - p1) / n1), 100 * 1.96 * math.sqrt(p2 * (1 - p2) / n2)]
p_txt = "p < 0.001" if p_valor < 0.001 else f"p = {p_valor:.3f}"
ax.bar(["A · Control", "B · Aviso 48 h"], vals, yerr=errs, capsize=5,
       color=[ROJO, DORADO], width=0.55, error_kw={"ecolor": "#e8eef7", "lw": 1.3})
ax.set_title(f"Test A/B ({p_txt})", fontsize=12, color="#e8eef7", loc="left")
ax.set_ylim(0, max(vals) * 1.5)
for v, e, x in zip(vals, errs, range(2)):
    ax.text(x, v + e + 0.7, f"{v:.1f}%", ha="center", fontsize=12.5, fontweight="bold")
ax.grid(axis="y", color="#1d2f4d"); ax.set_axisbelow(True)

ax = fig.add_subplot(gs[2, :])
ax.plot(sem.index, sem.values, color=CIAN, lw=2.2, marker="o", ms=4.5)
ax.fill_between(sem.index, sem.values, color=CIAN, alpha=0.12)
ax.axvline(pd.Timestamp("2026-08-15"), color=DORADO, ls="--", lw=1.4)
ax.annotate("inicio del experimento\n(50% recibe recordatorio)", xy=(pd.Timestamp("2026-08-15"), sem.max() * 0.97),
            xytext=(pd.Timestamp("2026-08-19"), sem.max() * 1.0), fontsize=10, color=DORADO)
ax.set_title("Tasa de no-show por semana (%)", fontsize=12, color="#e8eef7", loc="left")
ax.grid(color="#1d2f4d"); ax.set_axisbelow(True)


for ext in ("png", "svg"):
    fig.savefig(BASE / "visual" / f"dashboard-noshow.{ext}", dpi=170 if ext == "png" else None)
plt.close(fig)

# --- gráficos individuales (SVG) para el dashboard web ---
def save_chart(fig, name):
    fig.savefig(BASE / "visual" / name, format="svg")
    plt.close(fig)

fig, ax = plt.subplots(figsize=(5.2, 3.4))
ax.barh(q_canal.canal[::-1], q_canal.tasa_no_show_pct[::-1], color=[CIAN, "#38bdf8", MORADO, "#64748b"], height=0.62)
ax.set_title("No-show por canal de reserva (%)", fontsize=12, loc="left")
ax.set_xlim(0, max(q_canal.tasa_no_show_pct) * 1.28)
for v, y in zip(q_canal.tasa_no_show_pct[::-1], range(len(q_canal))):
    ax.text(v + 0.4, y, f"{v:.1f}%", va="center", fontsize=10.5)
ax.grid(axis="x", color="#1d2f4d"); ax.set_axisbelow(True)
fig.tight_layout(); save_chart(fig, "chart-canal.svg")

fig, ax = plt.subplots(figsize=(5.2, 3.4))
ax.bar(q_lead.tramo, q_lead.tasa_no_show_pct, color=MORADO, width=0.6)
ax.set_title("No-show según días de anticipación (%)", fontsize=12, loc="left")
ax.set_ylim(0, max(q_lead.tasa_no_show_pct) * 1.3)
for v, x in zip(q_lead.tasa_no_show_pct, range(len(q_lead))):
    ax.text(x, v + 0.5, f"{v:.1f}%", ha="center", fontsize=10.5)
ax.tick_params(axis="x", labelsize=9)
ax.grid(axis="y", color="#1d2f4d"); ax.set_axisbelow(True)
fig.tight_layout(); save_chart(fig, "chart-leadtime.svg")

fig, ax = plt.subplots(figsize=(5.2, 3.4))
ax.bar(["A · Sin recordatorio", "B · Recordatorio 48 h"], vals, yerr=errs, capsize=5,
       color=[ROJO, DORADO], width=0.5, error_kw={"ecolor": "#e8eef7", "lw": 1.3})
ax.set_title(f"Test A/B: {100*p1:.1f}% → {100*p2:.1f}%  ({p_txt})", fontsize=12, loc="left")
ax.set_ylim(0, max(vals) * 1.5)
for v, e, x in zip(vals, errs, range(2)):
    ax.text(x, v + e + 0.7, f"{v:.1f}%", ha="center", fontsize=12.5, fontweight="bold")
ax.grid(axis="y", color="#1d2f4d"); ax.set_axisbelow(True)
fig.tight_layout(); save_chart(fig, "chart-ab.svg")

fig, ax = plt.subplots(figsize=(10.4, 3.2))
ax.plot(sem.index, sem.values, color=CIAN, lw=2.2, marker="o", ms=4.5)
ax.fill_between(sem.index, sem.values, color=CIAN, alpha=0.12)
ax.axvline(pd.Timestamp("2026-08-15"), color=DORADO, ls="--", lw=1.4)
ax.annotate("inicio del experimento", xy=(pd.Timestamp("2026-08-15"), sem.max() * 0.97),
            xytext=(pd.Timestamp("2026-08-19"), sem.max() * 1.0), fontsize=10, color=DORADO)
ax.set_title("Tasa de no-show por semana (%)", fontsize=12, loc="left")
ax.grid(color="#1d2f4d"); ax.set_axisbelow(True)
fig.tight_layout(); save_chart(fig, "chart-tendencia.svg")
print("visual OK (dashboard + 4 charts)")
