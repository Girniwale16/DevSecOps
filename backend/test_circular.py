import duckdb
from backend.app.engine.mock_generator import generate_mock_from_schema

conn = duckdb.connect(':memory:')

# Two tables, circular ref
tables = [
    {"table_name": "company", "column_name": "id", "data_type": "int", "is_primary_key": True, "generator_type": "integer"},
    {"table_name": "company", "column_name": "head_user_id", "data_type": "int", "is_primary_key": False, "generator_type": "integer"},
    
    {"table_name": "users", "column_name": "id", "data_type": "int", "is_primary_key": True, "generator_type": "integer"},
    {"table_name": "users", "column_name": "company_id", "data_type": "int", "is_primary_key": False, "generator_type": "integer"}
]

relations = [
    {"from_table": "company", "from_column": "head_user_id", "to_table": "users", "to_column": "id"},
    {"from_table": "users", "from_column": "company_id", "to_table": "company", "to_column": "id"},
]

# Circular order testing
order_plan = ["company", "users"]

table_names = generate_mock_from_schema(
    tables, relations, 50, conn, seed=42, order=order_plan, row_counts={"company": 10, "users": 50}
)

df1 = conn.execute("SELECT * FROM company").df()
df2 = conn.execute("SELECT * FROM users").df()

print("Company")
print(df1)
print("\nUsers")
print(df2)

conn.close()
