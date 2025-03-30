-- models/staging/stg_tickets.sql
-- Initial staging model to clean and standardize the raw data
{{ config(materialized='view') }}

SELECT
    "Ticket ID" as ticket_id,
    "Category" as category,
    "Sub-Category" as subcategory,
    "Priority" as priority,
    TO_TIMESTAMP("Created Date", 'YYYY-MM-DD HH24:MI:SS') as created_date,
    CASE 
        WHEN "Resolved Date" = '' THEN NULL
        ELSE TO_TIMESTAMP("Resolved Date", 'YYYY-MM-DD HH24:MI:SS')
    END as resolved_date,
    "Status" as status,
    "Assigned Group" as assigned_group,
    "Technician" as technician,
    CASE
        WHEN "Resolution Time (Hrs)" = '' THEN NULL
        ELSE CAST("Resolution Time (Hrs)" AS FLOAT)
    END as resolution_time_hrs,
    "Customer Impact" as customer_impact,
    -- Extract date components
    EXTRACT(YEAR FROM TO_TIMESTAMP("Created Date", 'YYYY-MM-DD HH24:MI:SS')) as created_year,
    EXTRACT(MONTH FROM TO_TIMESTAMP("Created Date", 'YYYY-MM-DD HH24:MI:SS')) as created_month,
    EXTRACT(DAY FROM TO_TIMESTAMP("Created Date", 'YYYY-MM-DD HH24:MI:SS')) as created_day
FROM {{ source('raw', 'tickets') }}
WHERE "Ticket ID" IS NOT NULL
-- Remove duplicates by taking the first instance of each ticket ID
QUALIFY ROW_NUMBER() OVER (PARTITION BY "Ticket ID" ORDER BY "Created Date") = 1

-- models/marts/resolution_time_by_category.sql
-- Calculate average resolution time by category and priority
{{ config(materialized='table') }}

SELECT
    category,
    priority,
    COUNT(*) as ticket_count,
    AVG(resolution_time_hrs) as avg_resolution_time,
    STDDEV(resolution_time_hrs) as stddev_resolution_time,
    MIN(resolution_time_hrs) as min_resolution_time,
    MAX(resolution_time_hrs) as max_resolution_time
FROM {{ ref('stg_tickets') }}
WHERE resolved_date IS NOT NULL
  AND resolution_time_hrs IS NOT NULL
GROUP BY category, priority
ORDER BY category, priority

-- models/marts/closure_rate_by_group.sql
-- Calculate ticket closure rate by assigned group
{{ config(materialized='table') }}

WITH tickets_by_group AS (
    SELECT
        assigned_group,
        COUNT(*) as total_tickets,
        SUM(CASE WHEN status = 'Closed' OR status = 'Resolved' THEN 1 ELSE 0 END) as closed_tickets
    FROM {{ ref('stg_tickets') }}
    GROUP BY assigned_group
)

SELECT
    assigned_group,
    total_tickets,
    closed_tickets,
    ROUND((closed_tickets::FLOAT / total_tickets) * 100, 2) as closure_rate
FROM tickets_by_group
ORDER BY closure_rate DESC

-- models/marts/monthly_ticket_summary.sql
-- Create monthly summary of tickets and performance metrics
{{ config(materialized='table') }}

WITH monthly_data AS (
    SELECT
        created_year,
        created_month,
        TO_DATE(created_year || '-' || created_month || '-01', 'YYYY-MM-DD') as month_start_date,
        COUNT(*) as ticket_count,
        SUM(CASE WHEN status = 'Closed' OR status = 'Resolved' THEN 1 ELSE 0 END) as closed_tickets,
        AVG(resolution_time_hrs) as avg_resolution_time
    FROM {{ ref('stg_tickets') }}
    GROUP BY created_year, created_month
)

SELECT
    month_start_date,
    created_year,
    created_month,
    ticket_count,
    closed_tickets,
    avg_resolution_time,
    ROUND((closed_tickets::FLOAT / ticket_count) * 100, 2) as monthly_closure_rate,
    ticket_count - closed_tickets as backlog_count
FROM monthly_data
ORDER BY month_start_date

-- models/marts/open_tickets_by_priority.sql
-- Create view of currently open tickets by priority
{{ config(materialized='table') }}

SELECT
    priority,
    COUNT(*) as open_ticket_count,
    MIN(created_date) as oldest_ticket_date,
    MAX(created_date) as newest_ticket_date,
    EXTRACT(DAY FROM NOW() - MIN(created_date)) as oldest_ticket_age_days
FROM {{ ref('stg_tickets') }}
WHERE status NOT IN ('Closed', 'Resolved')
GROUP BY priority
ORDER BY 
    CASE
        WHEN priority = 'Critical' THEN 1
        WHEN priority = 'High' THEN 2
        WHEN priority = 'Medium' THEN 3
        WHEN priority = 'Low' THEN 4
        ELSE 5
    END
