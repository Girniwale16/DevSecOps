import sys
import os
sys.path.append(os.getcwd())
from app.engine.schema_parser import parse_ddl

ddl = """
CREATE TABLE customers (
    customer_id INT PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE,
    signup_date DATE
);

CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    customer_id INT NOT NULL,
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_amount DECIMAL(10, 2),
    status VARCHAR(50),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
"""

print("Starting parse...")
try:
    tables = parse_ddl(ddl, dialect="postgres")
    print(f"Parsed {len(tables)} tables")
    for t in tables:
        print(f"Table: {t['table_name']}")
        print(f"  Columns: {[c['name'] for c in t['columns']]}")
        print(f"  FKs: {t['foreign_keys']}")
except Exception as e:
    print(f"Error: {e}")
