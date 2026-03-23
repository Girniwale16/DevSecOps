import sqlglot
from sqlglot import exp

ddl = "CREATE TABLE orders (customer_id INT, FOREIGN KEY (customer_id) REFERENCES customers(customer_id))"
expression = sqlglot.parse_one(ddl)
for part in expression.this.expressions:
    if isinstance(part, exp.ForeignKey):
        print(f"FK REPR: {repr(part)}")
