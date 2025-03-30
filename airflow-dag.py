from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.sensors.external_task import ExternalTaskSensor
from airflow.hooks.postgres_hook import PostgresHook
import pandas as pd
import os

# Default arguments for DAG
default_args = {
    'owner': 'data_analyst',
    'depends_on_past': False,
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Define DAG
dag = DAG(
    'itsm_data_pipeline',
    default_args=default_args,
    description='Pipeline to process ServiceNow ITSM ticket data',
    schedule_interval='0 1 * * *',  # Run daily at 1:00 AM
    start_date=datetime(2025, 3, 1),
    catchup=False,
    tags=['itsm', 'servicenow'],
)

# Define the path to the CSV file
csv_file_path = "{{ var.value.itsm_data_path }}/tickets.csv"

# Task to ingest CSV data into PostgreSQL
def load_csv_to_postgres(**kwargs):
    """
    Load the ServiceNow ticket data from CSV into PostgreSQL
    """
    # Connect to PostgreSQL
    pg_hook = PostgresHook(postgres_conn_id='postgres_default')
    conn = pg_hook.get_conn()
    cursor = conn.cursor()
    
    # Create table if it doesn't exist
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS public.tickets (
        "Ticket ID" VARCHAR(50),
        "Category" VARCHAR(100),
        "Sub-Category" VARCHAR(100),
        "Priority" VARCHAR(20),
        "Created Date" VARCHAR(30),
        "Resolved Date" VARCHAR(30),
        "Status" VARCHAR(20),
        "Assigned Group" VARCHAR(100),
        "Technician" VARCHAR(100),
        "Resolution Time (Hrs)" VARCHAR(20),
        "Customer Impact" VARCHAR(20),
        PRIMARY KEY ("Ticket ID")
    );
    """
    cursor.execute(create_table_sql)
    conn.commit()
    
    # Truncate the table to ensure fresh data on each run
    cursor.execute("TRUNCATE TABLE public.tickets;")
    conn.commit()
    
    # Read CSV file
    df = pd.read_csv(csv_file_path)
    
    # Insert data into table
    for _, row in df.iterrows():
        insert_sql = """
        INSERT INTO public.tickets (
            "Ticket ID", "Category", "Sub-Category", "Priority", 
            "Created Date", "Resolved Date", "Status", 
            "Assigned Group", "Technician", "Resolution Time (Hrs)", 
            "Customer Impact"
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT ("Ticket ID") DO UPDATE 
        SET 
            "Category" = EXCLUDED."Category",
            "Sub-Category" = EXCLUDED."Sub-Category",
            "Priority" = EXCLUDED."Priority",
            "Created Date" = EXCLUDED."Created Date",
            "Resolved Date" = EXCLUDED."Resolved Date",
            "Status" = EXCLUDED."Status",
            "Assigned Group" = EXCLUDED."Assigned Group",
            "Technician" = EXCLUDED."Technician",
            "Resolution Time (Hrs)" = EXCLUDED."Resolution Time (Hrs)",
            "Customer Impact" = EXCLUDED."Customer Impact";
        """
        
        cursor.execute(insert_sql, (
            row["Ticket ID"], row["Category"], row["Sub-Category"], 
            row["Priority"], row["Created Date"], row["Resolved Date"], 
            row["Status"], row["Assigned Group"], row["Technician"], 
            row["Resolution Time (Hrs)"], row["Customer Impact"]
        ))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return "CSV data loaded into PostgreSQL successfully"

# Task to run DBT transformations
run_dbt_models = BashOperator(
    task_id='run_dbt_models',
    bash_command='cd {{ var.value.dbt_project_path }} && dbt run --profiles-dir .',
    dag=dag,
)

# Task to validate DBT models
run_dbt_tests = BashOperator(
    task_id='run_dbt_tests',
    bash_command='cd {{ var.value.dbt_project_path }} && dbt test --profiles-dir .',
    dag=dag,
)

# Task to check if all tables were created successfully
def validate_model_completion(**kwargs):
    """
    Verify that all expected tables were created by DBT
    """
    expected_tables = [
        'stg_tickets',
        'resolution_time_by_category',
        'closure_rate_by_group',
        'monthly_ticket_summary',
        'open_tickets_by_priority'
    ]
    
    pg_hook = PostgresHook(postgres_conn_id='postgres_default')
    
    for table in expected_tables:
        query = f"SELECT COUNT(*) FROM {table};"
        try:
            result = pg_hook.get_first(query)
            print(f"Table {table} exists with {result[0]} rows")
        except Exception as e:
            print(f"Error checking table {table}: {e}")
            return False
    
    return True

# Define tasks
ingest_csv_data = PythonOperator(
    task_id='ingest_csv_data',
    python_callable=load_csv_to_postgres,
    dag=dag,
)

validate_completion = PythonOperator(
    task_id='validate_completion',
    python_callable=validate_model_completion,
    dag=dag,
)

# Set task dependencies
ingest_csv_data >> run_dbt_models >> run_dbt_tests >> validate_completion
