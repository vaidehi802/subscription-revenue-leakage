-- ============================================================================
-- Subscription Revenue Leakage Analytics - SQL Analysis Layer
-- Dialect: SQLite (portable). Notes included for SQL Server / Postgres porting.
-- Build: loaded from CSVs by 02_build_database.py, which then runs this file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 0. CONFIG / ASSUMPTIONS
--    CLV horizon and gross margin assumption used in retention section.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_revenue_overview;
DROP VIEW IF EXISTS v_failed_payments;
DROP VIEW IF EXISTS v_failed_payment_by_plan;
DROP VIEW IF EXISTS v_failed_payment_reasons;
DROP VIEW IF EXISTS v_churn_monthly;
DROP VIEW IF EXISTS v_churn_reasons;
DROP VIEW IF EXISTS v_expired_subscriptions;
DROP VIEW IF EXISTS v_downgrade_analysis;
DROP VIEW IF EXISTS v_discount_leakage;
DROP VIEW IF EXISTS v_retention_metrics;
DROP VIEW IF EXISTS v_leakage_root_cause;
DROP VIEW IF EXISTS v_cohort_retention;
DROP VIEW IF EXISTS v_kpi_summary;

-- ============================================================================
-- 1. REVENUE OVERVIEW
--    Total revenue (collected net), MRR (active), ARR, active subscribers,
--    and total revenue leakage (sum of all loss categories).
-- ============================================================================
CREATE VIEW v_revenue_overview AS
WITH collected AS (
    SELECT SUM(net_amount) AS total_revenue_collected,
           SUM(discount_amount) AS total_discounts_given
    FROM fact_payments
    WHERE status = 'Success'
),
active_mrr AS (
    SELECT SUM(mrr) AS current_mrr,
           COUNT(*) AS active_subscribers
    FROM fact_subscriptions
    WHERE status = 'Active'
)
SELECT
    c.total_revenue_collected,
    a.current_mrr,
    a.current_mrr * 12              AS current_arr,
    a.active_subscribers,
    c.total_discounts_given
FROM collected c CROSS JOIN active_mrr a;

-- ============================================================================
-- 2. FAILED PAYMENT ANALYSIS
-- ============================================================================
-- 2a. Overall failed payment metrics + recoverable revenue
CREATE VIEW v_failed_payments AS
SELECT
    strftime('%Y-%m', payment_date)                       AS month,
    COUNT(*)                                              AS total_attempts,
    SUM(CASE WHEN status='Failed' THEN 1 ELSE 0 END)      AS failed_count,
    SUM(CASE WHEN status='Success' THEN 1 ELSE 0 END)     AS success_count,
    ROUND(100.0 * SUM(CASE WHEN status='Failed' THEN 1 ELSE 0 END) / COUNT(*), 2) AS failure_rate_pct,
    SUM(CASE WHEN status='Failed' THEN net_amount ELSE 0 END)                  AS failed_revenue,
    SUM(CASE WHEN status='Failed' AND recovered=1 THEN net_amount ELSE 0 END)  AS recovered_revenue,
    SUM(CASE WHEN status='Failed' AND recovered=0 THEN net_amount ELSE 0 END)  AS unrecovered_revenue
FROM fact_payments
GROUP BY strftime('%Y-%m', payment_date)
ORDER BY month;

-- 2b. Failed payments by plan
CREATE VIEW v_failed_payment_by_plan AS
SELECT
    pl.plan_name,
    pl.billing_cycle,
    COUNT(*)                                                AS failed_count,
    SUM(p.net_amount)                                       AS failed_revenue,
    SUM(CASE WHEN p.recovered=0 THEN p.net_amount ELSE 0 END) AS unrecovered_revenue
FROM fact_payments p
JOIN dim_plans pl ON p.plan_id = pl.plan_id
WHERE p.status='Failed'
GROUP BY pl.plan_name, pl.billing_cycle
ORDER BY failed_revenue DESC;

-- 2c. Failure reasons
CREATE VIEW v_failed_payment_reasons AS
SELECT
    failure_reason,
    COUNT(*)                AS occurrences,
    SUM(net_amount)         AS revenue_at_risk,
    ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),2) AS pct_of_failures
FROM fact_payments
WHERE status='Failed'
GROUP BY failure_reason
ORDER BY occurrences DESC;

-- ============================================================================
-- 3. CHURN ANALYSIS
-- ============================================================================
-- 3a. Monthly churn trend + churn rate (churned / active at start of month proxy)
CREATE VIEW v_churn_monthly AS
WITH churned AS (
    SELECT strftime('%Y-%m', end_date) AS month,
           COUNT(*) AS churned_subs,
           SUM(mrr) AS churned_mrr
    FROM fact_subscriptions
    WHERE status='Churned' AND end_date IS NOT NULL
    GROUP BY strftime('%Y-%m', end_date)
),
base AS (
    SELECT strftime('%Y-%m', start_date) AS month,
           COUNT(*) AS new_subs
    FROM fact_subscriptions
    GROUP BY strftime('%Y-%m', start_date)
)
SELECT
    COALESCE(c.month, b.month)        AS month,
    COALESCE(c.churned_subs,0)        AS churned_subs,
    COALESCE(c.churned_mrr,0)         AS churned_mrr_lost,
    COALESCE(b.new_subs,0)            AS new_subs
FROM churned c
LEFT JOIN base b ON c.month=b.month
ORDER BY month;

-- 3b. Churn reasons
CREATE VIEW v_churn_reasons AS
SELECT
    cancel_reason,
    COUNT(*)        AS churned_customers,
    SUM(mrr)        AS mrr_lost,
    SUM(mrr)*12     AS arr_lost,
    ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),2) AS pct_of_churn
FROM fact_subscriptions
WHERE status='Churned'
GROUP BY cancel_reason
ORDER BY churned_customers DESC;

-- ============================================================================
-- 4. EXPIRED SUBSCRIPTION ANALYSIS
-- ============================================================================
CREATE VIEW v_expired_subscriptions AS
SELECT
    c.segment,
    c.region,
    COUNT(*)            AS expired_subs,
    SUM(s.mrr)          AS missed_mrr,
    SUM(s.mrr)*12       AS missed_arr,
    ROUND(AVG(julianday(s.end_date)-julianday(s.start_date)),0) AS avg_lifetime_days
FROM fact_subscriptions s
JOIN dim_customers c ON s.customer_id=c.customer_id
WHERE s.status='Expired'
GROUP BY c.segment, c.region
ORDER BY missed_mrr DESC;

-- ============================================================================
-- 5. DOWNGRADE ANALYSIS
-- ============================================================================
CREATE VIEW v_downgrade_analysis AS
SELECT
    op.plan_name        AS from_plan,
    np.plan_name        AS to_plan,
    COUNT(*)            AS downgrade_count,
    SUM(d.mrr_lost)     AS monthly_mrr_lost,
    SUM(d.mrr_lost)*12  AS annual_revenue_lost
FROM fact_downgrades d
JOIN dim_plans op ON d.old_plan_id=op.plan_id
JOIN dim_plans np ON d.new_plan_id=np.plan_id
GROUP BY op.plan_name, np.plan_name
ORDER BY monthly_mrr_lost DESC;

-- ============================================================================
-- 6. DISCOUNT LEAKAGE ANALYSIS
-- ============================================================================
CREATE VIEW v_discount_leakage AS
SELECT
    pl.plan_name,
    CASE
        WHEN p.discount_pct=0 THEN 'No Discount'
        WHEN p.discount_pct<=0.15 THEN 'Low (<=15%)'
        WHEN p.discount_pct<=0.30 THEN 'Medium (16-30%)'
        ELSE 'High (>30%)'
    END                                  AS discount_band,
    COUNT(*)                             AS payments,
    SUM(p.gross_amount)                  AS gross_revenue,
    SUM(p.discount_amount)               AS discount_given,
    SUM(p.net_amount)                    AS net_revenue,
    ROUND(100.0*SUM(p.discount_amount)/NULLIF(SUM(p.gross_amount),0),2) AS effective_discount_pct
FROM fact_payments p
JOIN dim_plans pl ON p.plan_id=pl.plan_id
WHERE p.status='Success'
GROUP BY pl.plan_name, discount_band
ORDER BY discount_given DESC;

-- ============================================================================
-- 7. CUSTOMER RETENTION ANALYSIS
--    Retention rate, renewal rate, CLV.  CLV = ARPU * gross_margin * lifetime.
-- ============================================================================
CREATE VIEW v_retention_metrics AS
WITH totals AS (
    SELECT
        (SELECT COUNT(*) FROM fact_subscriptions) AS total_subs,
        (SELECT COUNT(*) FROM fact_subscriptions WHERE status='Active') AS active_subs,
        (SELECT COUNT(*) FROM fact_subscriptions WHERE status IN ('Churned','Expired')) AS lost_subs,
        (SELECT COUNT(*) FROM fact_renewals WHERE renewed=1) AS renewed_cnt,
        (SELECT COUNT(*) FROM fact_renewals) AS renewal_opps,
        (SELECT AVG(mrr) FROM fact_subscriptions WHERE status='Active') AS arpu,
        (SELECT AVG(julianday(COALESCE(end_date,'2024-12-31'))-julianday(start_date))/30.0
           FROM fact_subscriptions) AS avg_lifetime_months
)
SELECT
    active_subs,
    lost_subs,
    total_subs,
    ROUND(100.0*active_subs/total_subs,2)               AS retention_rate_pct,
    ROUND(100.0*renewed_cnt/NULLIF(renewal_opps,0),2)   AS renewal_rate_pct,
    ROUND(arpu,2)                                       AS arpu_monthly,
    ROUND(avg_lifetime_months,1)                        AS avg_lifetime_months,
    ROUND(arpu * 0.80 * avg_lifetime_months,2)          AS clv  -- 80% gross margin assumption
FROM totals;

-- ============================================================================
-- 8. REVENUE LEAKAGE ROOT CAUSE
--    Breaks total leakage into 5 categories (monthly recurring impact).
-- ============================================================================
CREATE VIEW v_leakage_root_cause AS
SELECT 'Churn Loss' AS leakage_category,
       (SELECT SUM(mrr) FROM fact_subscriptions WHERE status='Churned') AS monthly_loss
UNION ALL
SELECT 'Failed Payment Loss',
       (SELECT SUM(net_amount) FROM fact_payments WHERE status='Failed' AND recovered=0)
UNION ALL
SELECT 'Expired Subscription Loss',
       (SELECT SUM(mrr) FROM fact_subscriptions WHERE status='Expired')
UNION ALL
SELECT 'Downgrade Loss',
       (SELECT SUM(mrr_lost) FROM fact_downgrades)
UNION ALL
SELECT 'Discount Loss',
       (SELECT SUM(discount_amount) FROM fact_payments WHERE status='Success');

-- ============================================================================
-- 9. COHORT ANALYSIS
--    Signup-month cohorts; active count + retained MRR + retention % vs size.
-- ============================================================================
CREATE VIEW v_cohort_retention AS
WITH cohort AS (
    SELECT
        c.customer_id,
        strftime('%Y-%m', c.signup_date) AS cohort_month,
        s.status,
        s.mrr
    FROM dim_customers c
    JOIN fact_subscriptions s ON c.customer_id=s.customer_id
)
SELECT
    cohort_month,
    COUNT(*)                                              AS cohort_size,
    SUM(CASE WHEN status='Active' THEN 1 ELSE 0 END)      AS still_active,
    ROUND(100.0*SUM(CASE WHEN status='Active' THEN 1 ELSE 0 END)/COUNT(*),1) AS retention_pct,
    SUM(CASE WHEN status='Active' THEN mrr ELSE 0 END)    AS retained_mrr
FROM cohort
GROUP BY cohort_month
ORDER BY cohort_month;

-- ============================================================================
-- KPI SUMMARY (single-row card feed for dashboard)
-- ============================================================================
CREATE VIEW v_kpi_summary AS
SELECT
    (SELECT total_revenue_collected FROM v_revenue_overview)        AS total_revenue,
    (SELECT current_mrr FROM v_revenue_overview)                    AS mrr,
    (SELECT current_arr FROM v_revenue_overview)                    AS arr,
    (SELECT active_subscribers FROM v_revenue_overview)             AS active_subscribers,
    (SELECT retention_rate_pct FROM v_retention_metrics)            AS retention_rate_pct,
    (SELECT renewal_rate_pct FROM v_retention_metrics)              AS renewal_rate_pct,
    (SELECT clv FROM v_retention_metrics)                           AS clv,
    (SELECT SUM(monthly_loss) FROM v_leakage_root_cause)            AS total_revenue_leakage,
    (SELECT ROUND(100.0*(SELECT COUNT(*) FROM fact_subscriptions WHERE status IN ('Churned','Expired'))
        / (SELECT COUNT(*) FROM fact_subscriptions),2))             AS churn_rate_pct;
