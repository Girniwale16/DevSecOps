import duckdb
import os

DB_PATH = "data/studio_metadata.db"

def init_db():
    """Initializes the unified Studio metadata tables."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = duckdb.connect(DB_PATH)

    # Unified Project Header
    conn.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id UUID PRIMARY KEY,
        name VARCHAR,
        source_type VARCHAR, -- 'CSV' or 'DDL'
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Unified Table Model
    conn.execute("""
    CREATE TABLE IF NOT EXISTS tables (
        id UUID PRIMARY KEY,
        project_id UUID,
        name VARCHAR,
        file_path VARCHAR,
        row_count INTEGER,
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )
    """)

    # Unified Column Model (Merges DDL schema and CSV config)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS columns (
        id UUID PRIMARY KEY,
        table_id UUID,
        name VARCHAR,
        data_type VARCHAR,
        is_pk BOOLEAN DEFAULT FALSE,
        is_nullable BOOLEAN DEFAULT TRUE,
        is_pii BOOLEAN DEFAULT FALSE,
        generator_type VARCHAR DEFAULT 'auto',
        randomization_pct DOUBLE DEFAULT 0.0,
        FOREIGN KEY (table_id) REFERENCES tables(id)
    )
    """)

    # Unified Column Profiles (Statistical metadata)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS column_profiles (
        id UUID PRIMARY KEY,
        column_id UUID,
        null_count INTEGER,
        min_val VARCHAR,
        max_val VARCHAR,
        cardinality INTEGER,
        FOREIGN KEY (column_id) REFERENCES columns(id)
    )
    """)

    # Unified Relationships
    conn.execute("""
    CREATE TABLE IF NOT EXISTS relations (
        id UUID PRIMARY KEY,
        project_id UUID,
        from_table VARCHAR,
        from_column VARCHAR,
        to_table VARCHAR,
        to_column VARCHAR,
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )
    """)

    # ── Migrations — each ALTER in its own try/except ──────────────────────────
    _migrations = [
        # Relations extras
        "ALTER TABLE relations ADD COLUMN IF NOT EXISTS cardinality VARCHAR DEFAULT '1:N'",
        "ALTER TABLE relations ADD COLUMN IF NOT EXISTS is_optional BOOLEAN DEFAULT TRUE",
        # Column extras
        "ALTER TABLE columns ADD COLUMN IF NOT EXISTS allowed_values VARCHAR DEFAULT ''",
        "ALTER TABLE columns ADD COLUMN IF NOT EXISTS allowed_values_expanded VARCHAR DEFAULT ''",
        "ALTER TABLE columns ADD COLUMN IF NOT EXISTS expand_categories BOOLEAN DEFAULT FALSE",
        "ALTER TABLE columns ADD COLUMN IF NOT EXISTS output_format VARCHAR DEFAULT ''",
        # Column profile extras
        "ALTER TABLE column_profiles ADD COLUMN IF NOT EXISTS sd DOUBLE",
        "ALTER TABLE column_profiles ADD COLUMN IF NOT EXISTS variance DOUBLE",
        "ALTER TABLE column_profiles ADD COLUMN IF NOT EXISTS null_value_percent DOUBLE",
        # Time-series pattern model storage
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS is_timeseries BOOLEAN DEFAULT FALSE",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS timeseries_model TEXT DEFAULT NULL",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS ts_time_column VARCHAR DEFAULT NULL",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS ts_frequency VARCHAR DEFAULT NULL",
    ]
    for _stmt in _migrations:
        try:
            conn.execute(_stmt)
        except Exception:
            pass

    conn.close()

def get_db_connection():
    return duckdb.connect(DB_PATH)
