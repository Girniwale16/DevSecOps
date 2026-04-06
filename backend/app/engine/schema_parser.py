import sqlglot
from sqlglot import exp, parse_one
from typing import List, Dict, Any
import re


_COLUMN_TYPE_PATTERN = re.compile(
    r"(^|,)\s*(?!constraint\b|primary\b|foreign\b|unique\b|check\b)([^\"\(\)\n][^,\n]*?)(\s+"
    r"(?:character varying|varchar|char|text|numeric|decimal|double precision|double|float|real|bigint|integer|int|smallint|boolean|bool|date|timestamp|datetime)\b)",
    re.IGNORECASE,
)


def _cleanup_ddl(sql_text: str) -> str:
    text = str(sql_text or "")
    text = re.sub(r"\bENCODE\s+\w+\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCOLLATE\s+\w+\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bDISTKEY\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bSORTKEY\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    text = text.replace(" ,", ",").replace("( ", "(").replace(" )", ")")
    return text


def _quote_problematic_column_names(sql_text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        separator = match.group(1)
        raw_name = match.group(2).strip()
        type_part = match.group(3)
        if raw_name.startswith('"') and raw_name.endswith('"'):
            return match.group(0)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw_name):
            return match.group(0)
        escaped = raw_name.replace('"', '""')
        spacer = "" if separator == "," else separator
        return f'{spacer}"{escaped}"{type_part}'

    return re.sub(_COLUMN_TYPE_PATTERN, _replace, sql_text, flags=0)


def _split_top_level_csv(text: str) -> List[str]:
    items: List[str] = []
    current: List[str] = []
    depth = 0
    in_single = False
    in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif not in_single and not in_double:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth = max(0, depth - 1)
                current.append(ch)
            elif ch == "," and depth == 0:
                chunk = "".join(current).strip()
                if chunk:
                    items.append(chunk)
                current = []
            else:
                current.append(ch)
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _extract_create_table_blocks(sql_text: str) -> List[Dict[str, str]]:
    blocks: List[Dict[str, str]] = []
    pattern = re.compile(r"CREATE\s+TABLE\s+([^\(]+)\(", re.IGNORECASE)
    pos = 0
    while True:
        match = pattern.search(sql_text, pos)
        if not match:
            break
        raw_name = match.group(1).strip()
        start = match.end()
        depth = 1
        in_single = False
        in_double = False
        i = start
        while i < len(sql_text):
            ch = sql_text[i]
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif not in_single and not in_double:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        body = sql_text[start:i]
                        blocks.append({"table_name": raw_name, "body": body})
                        pos = i + 1
                        break
            i += 1
        else:
            break
    return blocks


def _normalize_identifier(raw: str) -> str:
    text = str(raw or "").strip()
    text = re.sub(r"\s+", " ", text)
    if "." in text:
        text = text.split(".")[-1].strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].replace('""', '"')
    return text


def _parse_column_line(line: str) -> Dict[str, Any] | None:
    stripped = line.strip().rstrip(",")
    lowered = stripped.lower()
    if not stripped or lowered.startswith(("constraint ", "primary key", "foreign key", "unique ", "check ")):
        return None

    m = re.match(r'^"([^"]+)"\s+(.+)$', stripped)
    if m:
        col_name = m.group(1)
        remainder = m.group(2).strip()
    else:
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s+(.+)$', stripped)
        if m:
            col_name = m.group(1)
            remainder = m.group(2).strip()
        else:
            # fallback for messy names until a known type keyword appears
            type_match = re.search(
                r'\b(character varying|varchar|char|text|numeric|decimal|double precision|double|float|real|bigint|integer|int|smallint|boolean|bool|date|timestamp|datetime)\b',
                stripped,
                re.IGNORECASE,
            )
            if not type_match:
                return None
            col_name = _normalize_identifier(stripped[: type_match.start()])
            remainder = stripped[type_match.start():].strip()

    type_match = re.match(
        r'((?:character varying|double precision)\s*\([^)]*\)|(?:varchar|char|numeric|decimal|double|float|real|bigint|integer|int|smallint|boolean|bool|date|timestamp|datetime|text)\s*(?:\([^)]*\))?)',
        remainder,
        re.IGNORECASE,
    )
    if not type_match:
        return None

    col_type = type_match.group(1).strip()
    constraints = remainder[type_match.end():].strip()
    return {
        "name": _normalize_identifier(col_name),
        "type": col_type,
        "is_nullable": "not null" not in constraints.lower(),
        "constraints": constraints,
    }


def _fallback_parse_ddl(sql_text: str) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    for block in _extract_create_table_blocks(sql_text):
        table_name = _normalize_identifier(block["table_name"])
        body = block["body"]
        columns: List[Dict[str, Any]] = []
        primary_keys: List[str] = []
        foreign_keys: List[Dict[str, Any]] = []

        for item in _split_top_level_csv(body):
            item_clean = item.strip()
            lowered = item_clean.lower()

            pk_match = re.search(r'primary\s+key\s*\(([^)]+)\)', item_clean, re.IGNORECASE)
            if pk_match:
                primary_keys.extend([_normalize_identifier(part) for part in _split_top_level_csv(pk_match.group(1))])
                continue

            fk_match = re.search(
                r'foreign\s+key\s*\(([^)]+)\)\s*references\s+([^\s(]+)\s*\(([^)]+)\)',
                item_clean,
                re.IGNORECASE,
            )
            if fk_match:
                foreign_keys.append(
                    {
                        "columns": [_normalize_identifier(part) for part in _split_top_level_csv(fk_match.group(1))],
                        "ref_table": _normalize_identifier(fk_match.group(2)),
                        "ref_columns": [_normalize_identifier(part) for part in _split_top_level_csv(fk_match.group(3))],
                    }
                )
                continue

            parsed_col = _parse_column_line(item_clean)
            if not parsed_col:
                continue

            constraints_lower = str(parsed_col.pop("constraints", "")).lower()
            col_name = str(parsed_col["name"])
            if "primary key" in constraints_lower:
                primary_keys.append(col_name)

            inline_fk = re.search(r'references\s+([^\s(]+)\s*\(([^)]+)\)', constraints_lower, re.IGNORECASE)
            if inline_fk:
                foreign_keys.append(
                    {
                        "columns": [col_name],
                        "ref_table": _normalize_identifier(inline_fk.group(1)),
                        "ref_columns": [_normalize_identifier(part) for part in _split_top_level_csv(inline_fk.group(2))],
                    }
                )

            columns.append(parsed_col)

        if columns:
            tables.append(
                {
                    "table_name": table_name,
                    "columns": columns,
                    "primary_keys": primary_keys,
                    "foreign_keys": foreign_keys,
                }
            )

    return tables

def parse_ddl(sql_text: str, dialect: str = "postgres") -> List[Dict[str, Any]]:
    """
    Parses DDL SQL text and extracts table definitions, columns, PKs, and FKs.
    Supports 'postgres' and 'mysql' dialects via sqlglot.
    """
    tables = []

    prepared_sql = _quote_problematic_column_names(_cleanup_ddl(sql_text))

    # Split by semicolon or try to parse the whole block
    # sqlglot.parse can handle multiple statements
    try:
        expressions = sqlglot.parse(prepared_sql, read=dialect)
    except Exception:
        expressions = None

    if expressions is None:
        return _fallback_parse_ddl(prepared_sql)
    
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
                
    if tables:
        return tables
    return _fallback_parse_ddl(prepared_sql)

def normalize_schema(raw_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Normalizes the raw parsed tables into a canonical schema model.
    """
    return {
        "tables": raw_tables,
        "version": "1.0"
    }
