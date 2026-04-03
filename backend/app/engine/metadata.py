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
    
    # Migration: Ensure cardinality and is_optional exist
    try:
        conn.execute("ALTER TABLE relations ADD COLUMN IF NOT EXISTS cardinality VARCHAR DEFAULT '1:N'")
        conn.execute("ALTER TABLE relations ADD COLUMN IF NOT EXISTS is_optional BOOLEAN DEFAULT TRUE")
    except:
        pass

    # Migration: Ensure allowed_values exist
    try:
        conn.execute("ALTER TABLE columns ADD COLUMN IF NOT EXISTS allowed_values VARCHAR DEFAULT ''")
        conn.execute("ALTER TABLE columns ADD COLUMN IF NOT EXISTS allowed_values_expanded VARCHAR DEFAULT ''")
        conn.execute("ALTER TABLE columns ADD COLUMN IF NOT EXISTS expand_categories BOOLEAN DEFAULT FALSE")
        conn.execute("ALTER TABLE columns ADD COLUMN IF NOT EXISTS output_format VARCHAR DEFAULT ''")
    except:
        pass

    # Migration: Add sd, variance, null_value_percent to column_profiles
    try:
        conn.execute("ALTER TABLE column_profiles ADD COLUMN IF NOT EXISTS sd DOUBLE")
        conn.execute("ALTER TABLE column_profiles ADD COLUMN IF NOT EXISTS variance DOUBLE")
        conn.execute("ALTER TABLE column_profiles ADD COLUMN IF NOT EXISTS null_value_percent DOUBLE")
    except:
        pass
        
    conn.close()

def get_db_connection():
    return duckdb.connect(DB_PATH)
