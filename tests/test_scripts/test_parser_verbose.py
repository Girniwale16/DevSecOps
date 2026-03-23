import sqlglot
from sqlglot import exp

ddl = """
CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    customer_id INT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
"""

expressions = sqlglot.parse(ddl, read="postgres")
for expression in expressions:
    if isinstance(expression, exp.Create):
        table_def = expression.this
        for part in table_def.expressions:
            if isinstance(part, exp.ForeignKey):
                print(f"Part: {part}")
                ref = part.args.get("reference")
                print(f"Ref: {ref}")
                print(f"Ref type: {type(ref)}")
                print(f"Ref expressions: {ref.expressions}")
                
                table_obj = ref.this
                print(f"TableObj: {table_obj}")
                print(f"TableObj type: {type(table_obj)}")
                print(f"TableObj expressions: {table_obj.expressions}")
                for i, ex in enumerate(table_obj.expressions):
                     print(f"  Expr {i}: {ex}, type: {type(ex)}")
