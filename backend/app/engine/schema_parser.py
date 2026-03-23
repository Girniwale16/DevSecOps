import sqlglot
from sqlglot import exp, parse_one
from typing import List, Dict, Any

def parse_ddl(sql_text: str, dialect: str = "postgres") -> List[Dict[str, Any]]:
    """
    Parses DDL SQL text and extracts table definitions, columns, PKs, and FKs.
    Supports 'postgres' and 'mysql' dialects via sqlglot.
    """
    tables = []
    
    # Split by semicolon or try to parse the whole block
    # sqlglot.parse can handle multiple statements
    expressions = sqlglot.parse(sql_text, read=dialect)
    
    for expression in expressions:
        if isinstance(expression, exp.Create):
            # We are looking for CREATE TABLE
            if expression.args.get("kind") == "TABLE":
                table_def = expression.this
                table_name = table_def.this.name
                
                columns = []
                primary_keys = []
                foreign_keys = []
                
                # Iterate through column and constraint definitions
                for part in table_def.expressions:
                    if isinstance(part, exp.ColumnDef):
                        col_name = part.this.name
                        col_type = part.args.get("kind")
                        
                        constraints = part.args.get("constraints", [])
                        
                        is_not_null = any(isinstance(c.kind, exp.NotNullColumnConstraint) for c in constraints)
                        is_pk = any(isinstance(c.kind, exp.PrimaryKeyColumnConstraint) for c in constraints)

                        columns.append({
                            "name": col_name,
                            "type": str(col_type),
                            "is_nullable": not is_not_null
                        })
                        
                        if is_pk:
                            primary_keys.append(col_name)
                            
                        # Check for inline foreign key references (e.g. user_id INT REFERENCES users(id))
                        for c in constraints:
                            if isinstance(c.kind, exp.Reference):
                                schema_obj = c.kind.this
                                if isinstance(schema_obj, exp.Schema):
                                    table_obj = schema_obj.this
                                    ref_table = table_obj.this.this if hasattr(table_obj.this, 'this') else table_obj.this
                                    ref_cols = [col.this if isinstance(col.this, str) else col.this.this for col in schema_obj.expressions if hasattr(col, 'this')]
                                    foreign_keys.append({
                                        "columns": [col_name],
                                        "ref_table": str(ref_table),
                                        "ref_columns": ref_cols
                                    })
                                    
                    elif isinstance(part, exp.PrimaryKey):
                        # Multi-column primary key or named PK constraint
                        for col in part.expressions:
                            if isinstance(col, exp.Column):
                                primary_keys.append(col.name)
                            elif isinstance(col, exp.Identifier):
                                primary_keys.append(col.this)
                                
                    elif isinstance(part, exp.ForeignKey):
                        # Foreign key constraint
                        cols = [c.this if isinstance(c.this, str) else c.this.this for c in part.expressions if hasattr(c, 'this')]
                        ref_obj = part.args.get("reference")
                        if ref_obj:
                            # In sqlglot, Reference.this is often a Schema object
                            schema_obj = ref_obj.this
                            if isinstance(schema_obj, exp.Schema):
                                table_obj = schema_obj.this
                                ref_table = table_obj.this.this if hasattr(table_obj.this, 'this') else table_obj.this
                                ref_cols = [c.this if isinstance(c.this, str) else c.this.this for c in schema_obj.expressions if hasattr(c, 'this')]
                            elif isinstance(schema_obj, exp.Table):
                                ref_table = schema_obj.this.this if hasattr(schema_obj.this, 'this') else schema_obj.this
                                ref_cols = [c.this if isinstance(c.this, str) else c.this.this for c in schema_obj.expressions if hasattr(c, 'this')]
                            else:
                                ref_table = schema_obj
                                ref_cols = []
                            
                            foreign_keys.append({
                                "columns": cols,
                                "ref_table": str(ref_table),
                                "ref_columns": ref_cols
                            })
                
                tables.append({
                    "table_name": table_name,
                    "columns": columns,
                    "primary_keys": primary_keys,
                    "foreign_keys": foreign_keys
                })
                
    return tables

def normalize_schema(raw_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Normalizes the raw parsed tables into a canonical schema model.
    """
    return {
        "tables": raw_tables,
        "version": "1.0"
    }
