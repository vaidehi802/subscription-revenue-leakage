"""
Subscription Revenue Leakage - Synthetic Data Generator
Generates realistic customer, subscription, payment, and event data
spanning Jan 2023 - Dec 2024 for revenue leakage analytics.
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import random
import os

np.random.seed(42)
random.seed(42)

OUT = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(OUT, exist_ok=True)

START = datetime(2023, 1, 1)
END = datetime(2024, 12, 31)
N_CUSTOMERS = 4000

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
PLANS = pd.DataFrame([
    {"plan_id": "P_BASIC_M",   "plan_name": "Basic",      "billing_cycle": "Monthly", "monthly_price": 19,  "tier_rank": 1},
    {"plan_id": "P_PRO_M",     "plan_name": "Pro",        "billing_cycle": "Monthly", "monthly_price": 49,  "tier_rank": 2},
    {"plan_id": "P_BUS_M",     "plan_name": "Business",   "billing_cycle": "Monthly", "monthly_price": 99,  "tier_rank": 3},
    {"plan_id": "P_ENT_M",     "plan_name": "Enterprise", "billing_cycle": "Monthly", "monthly_price": 249, "tier_rank": 4},
    {"plan_id": "P_BASIC_A",   "plan_name": "Basic",      "billing_cycle": "Annual",  "monthly_price": 16,  "tier_rank": 1},
    {"plan_id": "P_PRO_A",     "plan_name": "Pro",        "billing_cycle": "Annual",  "monthly_price": 41,  "tier_rank": 2},
    {"plan_id": "P_BUS_A",     "plan_name": "Business",   "billing_cycle": "Annual",  "monthly_price": 82,  "tier_rank": 3},
    {"plan_id": "P_ENT_A",     "plan_name": "Enterprise", "billing_cycle": "Annual",  "monthly_price": 208, "tier_rank": 4},
])

REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]
SEGMENTS = ["SMB", "Mid-Market", "Enterprise", "Startup"]
ACQ_CHANNELS = ["Organic Search", "Paid Ads", "Referral", "Partner", "Direct", "Social"]
CHURN_REASONS = ["Too Expensive", "Missing Features", "Switched Competitor", "No Longer Needed",
                 "Poor Support", "Technical Issues", "Budget Cuts"]
FAIL_REASONS = ["Insufficient Funds", "Card Expired", "Card Declined", "Fraud Suspected",
                "Processing Error", "Invalid Card Details", "Bank Authentication Failed"]

# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
def rand_date(start, end):
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))

customers = []
for i in range(1, N_CUSTOMERS + 1):
    signup = rand_date(START, END - timedelta(days=30))
    customers.append({
        "customer_id": f"C{i:05d}",
        "signup_date": signup,
        "region": np.random.choice(REGIONS, p=[0.40, 0.25, 0.18, 0.10, 0.07]),
        "segment": np.random.choice(SEGMENTS, p=[0.45, 0.30, 0.10, 0.15]),
        "acquisition_channel": np.random.choice(ACQ_CHANNELS, p=[0.30, 0.22, 0.18, 0.10, 0.12, 0.08]),
        "country_age_days": (END - signup).days,
    })
customers = pd.DataFrame(customers)

# ---------------------------------------------------------------------------
# Subscriptions  (one active lifecycle per customer, some churn/expire/downgrade)
# ---------------------------------------------------------------------------
subs = []
sub_seq = 1
for _, c in customers.iterrows():
    start_plan = np.random.choice(PLANS["plan_id"], p=[0.22,0.20,0.10,0.05,0.15,0.16,0.08,0.04])
    plan_row = PLANS[PLANS.plan_id == start_plan].iloc[0]
    cycle = plan_row.billing_cycle
    sub_start = c.signup_date

    # Determine an outcome for the subscription
    # status mix: active, churned (cancelled), expired (no renewal), downgraded (still active lower tier)
    outcome = np.random.choice(
        ["active", "churned", "expired", "downgraded", "payment_failed_churn"],
        p=[0.46, 0.22, 0.14, 0.12, 0.06]
    )

    # subscription end / current state
    months_active = np.random.randint(1, 24)
    period = timedelta(days=30 if cycle == "Monthly" else 365)

    sub = {
        "subscription_id": f"S{sub_seq:06d}",
        "customer_id": c.customer_id,
        "plan_id": start_plan,
        "billing_cycle": cycle,
        "start_date": sub_start,
        "status": "Active",
        "end_date": pd.NaT,
        "cancel_reason": None,
        "current_monthly_value": plan_row.monthly_price * (12 if cycle == "Annual" else 1) / (12 if cycle=="Annual" else 1),
        "mrr": plan_row.monthly_price,  # normalized monthly recurring revenue
        "auto_renew": np.random.choice([1, 0], p=[0.8, 0.2]),
        "outcome": outcome,
    }

    end_date = sub_start + timedelta(days=30*months_active)
    if end_date > END:
        end_date = END

    if outcome == "churned" or outcome == "payment_failed_churn":
        sub["status"] = "Churned"
        sub["end_date"] = end_date
        sub["cancel_reason"] = (np.random.choice(CHURN_REASONS, p=[0.22,0.18,0.15,0.15,0.12,0.10,0.08])
                                if outcome == "churned" else "Payment Failure")
    elif outcome == "expired":
        sub["status"] = "Expired"
        sub["end_date"] = end_date
        sub["auto_renew"] = 0
    elif outcome == "downgraded":
        sub["status"] = "Active"  # still active but at lower MRR
    # active stays active

    subs.append(sub)
    sub_seq += 1

subs = pd.DataFrame(subs)

# ---------------------------------------------------------------------------
# Downgrade events (for downgraded subscriptions, record old->new plan)
# ---------------------------------------------------------------------------
downgrades = []
dg_seq = 1
dg_subs = subs[subs.outcome == "downgraded"]
for _, s in dg_subs.iterrows():
    old_plan = PLANS[PLANS.plan_id == s.plan_id].iloc[0]
    # find a lower tier plan, same cycle
    lower = PLANS[(PLANS.tier_rank < old_plan.tier_rank) & (PLANS.billing_cycle == old_plan.billing_cycle)]
    if len(lower) == 0:
        continue
    new_plan = lower.sample(1).iloc[0]
    dg_date = s.start_date + timedelta(days=random.randint(30, 400))
    if dg_date > END:
        dg_date = END
    downgrades.append({
        "downgrade_id": f"DG{dg_seq:05d}",
        "subscription_id": s.subscription_id,
        "customer_id": s.customer_id,
        "downgrade_date": dg_date,
        "old_plan_id": old_plan.plan_id,
        "new_plan_id": new_plan.plan_id,
        "old_mrr": old_plan.monthly_price,
        "new_mrr": new_plan.monthly_price,
        "mrr_lost": old_plan.monthly_price - new_plan.monthly_price,
    })
    # update subscription current mrr to new (lower) value
    subs.loc[subs.subscription_id == s.subscription_id, "mrr"] = new_plan.monthly_price
    subs.loc[subs.subscription_id == s.subscription_id, "plan_id"] = new_plan.plan_id
    dg_seq += 1
downgrades = pd.DataFrame(downgrades)

# ---------------------------------------------------------------------------
# Payments / Invoices (monthly billing events with success/failure)
# ---------------------------------------------------------------------------
payments = []
pay_seq = 1
for _, s in subs.iterrows():
    plan = PLANS[PLANS.plan_id == s.plan_id].iloc[0]
    cycle_days = 30 if s.billing_cycle == "Monthly" else 365
    charge_amount = plan.monthly_price * (12 if s.billing_cycle == "Annual" else 1)

    billing_end = s.end_date if pd.notna(s.end_date) else END
    d = s.start_date
    while d <= billing_end:
        # base failure probability, higher for payment_failed_churn cohort and near the end
        base_fail = 0.06
        if s.outcome == "payment_failed_churn":
            base_fail = 0.35
        is_fail = np.random.random() < base_fail
        # apply discount on some payments
        discount_pct = 0.0
        if np.random.random() < 0.28:
            discount_pct = np.random.choice([0.10, 0.15, 0.20, 0.25, 0.30, 0.50],
                                            p=[0.30,0.25,0.20,0.12,0.08,0.05])
        gross = charge_amount
        discount_amt = round(gross * discount_pct, 2)
        net = round(gross - discount_amt, 2)

        rec = {
            "payment_id": f"PAY{pay_seq:07d}",
            "subscription_id": s.subscription_id,
            "customer_id": s.customer_id,
            "plan_id": s.plan_id,
            "billing_cycle": s.billing_cycle,
            "payment_date": d,
            "gross_amount": gross,
            "discount_pct": discount_pct,
            "discount_amount": discount_amt,
            "net_amount": net,
            "status": "Failed" if is_fail else "Success",
            "failure_reason": (np.random.choice(FAIL_REASONS, p=[0.30,0.22,0.18,0.06,0.10,0.08,0.06])
                               if is_fail else None),
            "retry_count": (np.random.choice([0,1,2,3], p=[0.4,0.3,0.2,0.1]) if is_fail else 0),
            "recovered": (np.random.choice([1,0], p=[0.45,0.55]) if is_fail else 0),
        }
        payments.append(rec)
        pay_seq += 1
        d = d + timedelta(days=cycle_days)

payments = pd.DataFrame(payments)

# ---------------------------------------------------------------------------
# Renewals (for annual & monthly subs that reached renewal points)
# ---------------------------------------------------------------------------
renewals = []
rn_seq = 1
for _, s in subs.iterrows():
    cycle_days = 30 if s.billing_cycle == "Monthly" else 365
    billing_end = s.end_date if pd.notna(s.end_date) else END
    # renewal opportunities = number of cycle boundaries
    n_cycles = max(0, int((billing_end - s.start_date).days // cycle_days))
    for k in range(1, n_cycles + 1):
        renew_date = s.start_date + timedelta(days=cycle_days * k)
        if renew_date > END:
            break
        # renewed unless expired/churned at that boundary
        if s.status in ("Expired", "Churned") and renew_date >= billing_end - timedelta(days=cycle_days):
            renewed = 0
        else:
            renewed = np.random.choice([1, 0], p=[0.88, 0.12])
        renewals.append({
            "renewal_id": f"RN{rn_seq:06d}",
            "subscription_id": s.subscription_id,
            "customer_id": s.customer_id,
            "renewal_date": renew_date,
            "plan_id": s.plan_id,
            "renewed": renewed,
            "mrr": s.mrr,
        })
        rn_seq += 1
renewals = pd.DataFrame(renewals)

# ---------------------------------------------------------------------------
# Save raw CSVs
# ---------------------------------------------------------------------------
PLANS.to_csv(f"{OUT}/dim_plans.csv", index=False)
customers.to_csv(f"{OUT}/dim_customers.csv", index=False)
subs.drop(columns=["outcome"]).to_csv(f"{OUT}/fact_subscriptions.csv", index=False)
payments.to_csv(f"{OUT}/fact_payments.csv", index=False)
downgrades.to_csv(f"{OUT}/fact_downgrades.csv", index=False)
renewals.to_csv(f"{OUT}/fact_renewals.csv", index=False)

print("=== Data Generation Complete ===")
print(f"Customers:     {len(customers):,}")
print(f"Subscriptions: {len(subs):,}")
print(f"Payments:      {len(payments):,}")
print(f"Downgrades:    {len(downgrades):,}")
print(f"Renewals:      {len(renewals):,}")
print(f"\nStatus mix:\n{subs.status.value_counts()}")
print(f"\nPayment status:\n{payments.status.value_counts()}")
