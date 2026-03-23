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
        ref = part.args.get("reference")
        print(f"Ref type: {type(ref)}")
        print(f"Ref args keys: {ref.args.keys()}")
        print(f"Ref expressions: {ref.expressions}")
        
        table_obj = ref.this
        print(f"TableObj type: {type(table_obj)}")
        print(f"TableObj args: {table_obj.args.keys()}")
        print(f"TableObj expressions: {table_obj.expressions}")
