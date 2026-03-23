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
        print(f"Part type: {type(part)}")
        print(f"Expressions: {part.expressions}")
        for c in part.expressions:
            print(f"  Expr type: {type(c)}, name attr: {getattr(c, 'name', 'N/A')}, this attr: {getattr(c, 'this', 'N/A')}")
        
        ref = part.args.get("reference")
        print(f"Reference: {ref}")
        print(f"  Ref type: {type(ref)}")
        print(f"  Ref this: {ref.this}")
        print(f"  Ref expressions: {ref.expressions}")
        for rc in ref.expressions:
             print(f"    RefExpr type: {type(rc)}, name attr: {getattr(rc, 'name', 'N/A')}")
