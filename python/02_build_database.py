"""
Builds the SQLite database from generated CSVs, applies analysis_views.sql,
and exports each analytical view to CSV for Power BI ingestion.
"""
import sqlite3, os, pandas as pd

BASE = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(BASE, "data")
SQL  = os.path.join(BASE, "sql", "analysis_views.sql")
PBI  = os.path.join(BASE, "powerbi", "datasets")
os.makedirs(PBI, exist_ok=True)
DB = os.path.join(DATA, "subscription.db")

if os.path.exists(DB):
    os.remove(DB)
con = sqlite3.connect(DB)

# Load raw tables
tables = {
    "dim_plans": "dim_plans.csv",
    "dim_customers": "dim_customers.csv",
    "fact_subscriptions": "fact_subscriptions.csv",
    "fact_payments": "fact_payments.csv",
    "fact_downgrades": "fact_downgrades.csv",
    "fact_renewals": "fact_renewals.csv",
}
for t, f in tables.items():
    df = pd.read_csv(os.path.join(DATA, f))
    df.to_sql(t, con, if_exists="replace", index=False)
    print(f"Loaded {t}: {len(df):,} rows")

# Apply views
with open(SQL) as fh:
    con.executescript(fh.read())
con.commit()

# Export views for Power BI
views = [
    "v_revenue_overview", "v_failed_payments", "v_failed_payment_by_plan",
    "v_failed_payment_reasons", "v_churn_monthly", "v_churn_reasons",
    "v_expired_subscriptions", "v_downgrade_analysis", "v_discount_leakage",
    "v_retention_metrics", "v_leakage_root_cause", "v_cohort_retention",
    "v_kpi_summary",
]
print("\n=== Exporting analytical views ===")
for v in views:
    df = pd.read_sql(f"SELECT * FROM {v}", con)
    df.to_csv(os.path.join(PBI, f"{v}.csv"), index=False)
    print(f"  {v}: {len(df)} rows")

# Also copy raw fact/dim tables to powerbi datasets for star-schema modeling
for t, f in tables.items():
    pd.read_csv(os.path.join(DATA, f)).to_csv(os.path.join(PBI, f), index=False)

print("\n=== KPI Summary ===")
print(pd.read_sql("SELECT * FROM v_kpi_summary", con).T)
print("\n=== Revenue Leakage Root Cause ===")
print(pd.read_sql("SELECT * FROM v_leakage_root_cause", con).to_string(index=False))
con.close()
