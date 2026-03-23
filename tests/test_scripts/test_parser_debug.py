import sqlglot
from sqlglot import exp

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

dialect = "postgres"
expressions = sqlglot.parse(ddl, read=dialect)
for expression in expressions:
    if isinstance(expression, exp.Create):
        table_def = expression.this
        table_name = table_def.this.name
        print(f"Table: {table_name}")
        for part in table_def.expressions:
            if isinstance(part, exp.ForeignKey):
                print(f"  Found FK part: {part}")
                cols = [c.name for c in part.expressions if hasattr(c, 'name')]
                ref_table = part.args.get('reference').this.name
                ref_cols = [c.name for c in part.args.get('reference').expressions if hasattr(c, 'name')]
                print(f"    Cols: {cols} -> {ref_table}({ref_cols})")
