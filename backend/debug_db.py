import duckdb
import pandas as pd

# Check metadata DB to see what 'generator_type' was saved
conn = duckdb.connect('data/studio_metadata.db')
df = conn.execute("""
    SELECT t.name as table_name, c.name as col_name, c.data_type, c.generator_type 
    FROM columns c JOIN tables t ON c.table_id = t.id 
    JOIN projects p ON t.project_id = p.id 
    WHERE p.source_type = 'SCHEMA'
""").df()
print("--- Schema Columns in DB ---")
print(df)
conn.close()
