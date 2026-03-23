import sqlglot
from sqlglot import exp

ddl = """
CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    customer_id INT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
"""

expression = sqlglot.parse_one(ddl, read="postgres")
table_def = expression.this
for part in table_def.expressions:
    if isinstance(part, exp.ForeignKey):
        for c in part.expressions:
            print(f"FK Column: {c.this if hasattr(c, 'this') else c}")
        
        ref = part.args.get("reference")
        table_obj = ref.this
        print(f"Ref Table Name: {table_obj.this.this if hasattr(table_obj.this, 'this') else table_obj.this}")
        for rc in table_obj.expressions:
            print(f"Ref Table Col: {rc.this if hasattr(rc, 'this') else rc}")
