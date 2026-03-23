import duckdb
import random
import re
from faker import Faker
from typing import List, Dict, Any

def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _has_token(name: str, tokens: List[str]) -> bool:
    normalized = _normalize_name(name)
    return any(token in normalized for token in tokens)


def _find_product_anchor(table_cols: List[Dict[str, Any]]) -> str | None:
    for col in table_cols:
        if _has_token(str(col.get("column_name", "")), ["product", "item", "sku", "goods", "merchandise"]):
            return str(col.get("column_name"))
    return None


def _find_quantity_columns(table_cols: List[Dict[str, Any]]) -> List[str]:
    quantity_cols = []
    for col in table_cols:
        if _has_token(str(col.get("column_name", "")), ["quantity", "qty", "units", "unit_count"]):
            quantity_cols.append(str(col.get("column_name")))
    return quantity_cols


def _find_unit_price_columns(table_cols: List[Dict[str, Any]]) -> List[str]:
    price_cols = []
    for col in table_cols:
        name = str(col.get("column_name", ""))
        normalized = _normalize_name(name)
        if any(token in normalized for token in ["price", "unit_price", "selling_price", "sale_price", "list_price", "cost", "rate", "mrp"]):
            price_cols.append(name)
    return price_cols


def _find_amount_columns(table_cols: List[Dict[str, Any]]) -> List[str]:
    amount_cols = []
    for col in table_cols:
        name = str(col.get("column_name", ""))
        normalized = _normalize_name(name)
        if any(token in normalized for token in ["amount", "total", "subtotal", "net_amount", "gross_amount", "line_total", "extended_price"]):
            amount_cols.append(name)
    return amount_cols


def _build_product_price_sql(q_product: str) -> str:
    return (
        "ROUND("
        "1 + (ABS(HASH(LOWER(TRIM(CAST("
        f"{q_product}"
        " AS VARCHAR))))) % 5000) / 100.0,"
        "2)"
    )


def _apply_product_price_consistency(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    table_cols: List[Dict[str, Any]],
) -> None:
    product_col = _find_product_anchor(table_cols)
    unit_price_cols = _find_unit_price_columns(table_cols)
    amount_cols = _find_amount_columns(table_cols)
    quantity_cols = _find_quantity_columns(table_cols)
    if not product_col or (not unit_price_cols and not amount_cols):
        return

    q_table = _quote_ident(table_name)
    q_product = _quote_ident(product_col)
    product_price_sql = _build_product_price_sql(q_product)

    for price_col in unit_price_cols:
        q_price = _quote_ident(price_col)
        conn.execute(
            f"""
            UPDATE {q_table}
            SET {q_price} = CASE
                WHEN {q_product} IS NULL THEN NULL
                ELSE {product_price_sql}
            END
            """
        )

    quantity_expr = "1"
    if quantity_cols:
        q_quantity = _quote_ident(quantity_cols[0])
        quantity_expr = f"LEAST(GREATEST(COALESCE(TRY_CAST({q_quantity} AS DOUBLE), 1), 1), 25)"

    for amount_col in amount_cols:
        q_amount = _quote_ident(amount_col)
        amount_expr = product_price_sql
        if quantity_cols:
            amount_expr = f"ROUND(({product_price_sql}) * {quantity_expr}, 2)"
        conn.execute(
            f"""
            UPDATE {q_table}
            SET {q_amount} = CASE
                WHEN {q_product} IS NULL THEN NULL
                ELSE {amount_expr}
            END
            """
        )


def _parse_weighted_allowed_values(raw: Any) -> List[tuple[str, float]]:
    if isinstance(raw, list):
        entries = [(str(v).strip(), 1.0) for v in raw if str(v).strip()]
    else:
        text = str(raw or "").strip()
        if not text:
            return []
        entries = []
        for part in [p.strip() for p in re.split(r"[\n,;]+", text) if p.strip()]:
            value = part
            weight = 1.0
            if "|" in part:
                value_part, weight_part = part.rsplit("|", 1)
                value = value_part.strip()
                try:
                    weight = max(0.0, float(weight_part.strip()))
                except Exception:
                    weight = 1.0
            if value:
                entries.append((value, weight))

    deduped = []
    seen = set()
    for value, weight in entries:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append((value, weight))
    if not deduped:
        return []
    total = sum(weight for _, weight in deduped)
    if total <= 0:
        uniform = 1.0 / len(deduped)
        return [(value, uniform) for value, _ in deduped]
    return [(value, weight / total) for value, weight in deduped]

def generate_pool(faker_func_name: str, size: int, seed: int) -> List[Any]:
    fake = Faker()
    fake.seed_instance(seed)

    func = getattr(fake, faker_func_name)
    return [func() for _ in range(size)]

def generate_mock_from_schema(
    tables: List[Dict[str, Any]], 
    relations: List[Dict[str, Any]], 
    num_rows: int, 
    conn: duckdb.DuckDBPyConnection,
    seed: int = 42,
    order: List[str] = None,
    row_counts: Dict[str, int] = None,
    table_seeds: Dict[str, int] = None,
):
    """
    TURBO DATA ENGINE v2
    - Micro-chunking (100k batches)
    - Vectorized SQL generation (Zero Python loops for rows)
    - Optimized Semantic Pooling
    """
    random.seed(seed)
    generation_order = order if order else sorted(list(set(t['table_name'] for t in tables)))
    
    # Safety: Allow scalar subqueries to pick random row if they accidentally match multiple
    conn.execute("SET scalar_subquery_error_on_multiple_rows=false")
    conn.execute("CREATE MACRO IF NOT EXISTS row_random(x) AS CASE WHEN x >= 0 THEN random() ELSE random() END")
    
    for table_name in generation_order:
        table_cols = [c for c in tables if c['table_name'] == table_name]
        rows_to_gen = row_counts.get(table_name, num_rows) if row_counts else num_rows
        current_seed = int((table_seeds or {}).get(table_name, seed))
        random.seed(current_seed)
        q_table_name = _quote_ident(table_name)
        
        # 1. Create table
        col_defs = []
        for col in table_cols:
            c_type = col['data_type'].upper()
            if "INT" in c_type: dtype = "BIGINT"
            elif "BOOL" in c_type: dtype = "BOOLEAN"
            elif "DATE" in c_type and "TIME" not in c_type: dtype = "DATE"
            elif "DATE" in c_type or "TIME" in c_type: dtype = "TIMESTAMP"
            elif "FLOAT" in c_type or "DECIMAL" in c_type or "REAL" in c_type: dtype = "DOUBLE"
            elif "JSON" in c_type or "BLOB" in c_type or "TEXT" in c_type: dtype = "VARCHAR"
            else: dtype = "VARCHAR"
            col_defs.append(f'"{col["column_name"]}" {dtype}')
        
        conn.execute(f"CREATE TABLE {q_table_name} ({', '.join(col_defs)})")

        # 2. Setup semantic pools
        sql_cols = []
        for col in table_cols:
            col_name = col['column_name']
            c_type = col['data_type'].upper()
            is_pk = col['is_primary_key']
            is_nullable = col.get('is_nullable', False)
            gen_type = col.get('generator_type', 'auto').lower()
            fk_rel = next((r for r in relations if r['from_table'] == table_name and r['from_column'] == col_name), None)

            base_sql = None
            
            if fk_rel:
                parent_table = fk_rel['to_table']
                parent_col = fk_rel['to_column']
                q_parent_table = _quote_ident(parent_table)
                fk_pool_table = f"fk_pool_{table_name}_{col_name}"
                q_fk_pool_table = _quote_ident(fk_pool_table)
                
                # Check if parent table exists and has rows
                parent_exists = False
                parent_count = 0
                
                try:
                    exists_df = conn.execute(
                        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", 
                        (parent_table,)
                    ).df()
                    if not exists_df.empty:
                        count_df = conn.execute(f"SELECT count(*) as c FROM {q_parent_table}").df()
                        parent_count = int(count_df.iloc[0]['c'])
                        parent_exists = True
                except Exception:
                    pass
                
                if parent_exists and parent_count > 0:
                    # Materialize index pool for O(1) sampling instead of scanning parent for every row
                    conn.execute(f"""
                        CREATE TEMP TABLE {q_fk_pool_table} AS 
                        SELECT "{parent_col}" AS val, row_number() OVER() - 1 AS id 
                        FROM {q_parent_table}
                    """)
                    base_sql = f'(SELECT val FROM {q_fk_pool_table} WHERE id = CAST(row_random(i) * {parent_count-1} AS INT) LIMIT 1)'
                else:
                    # Fallback (Circular dependency or empty parent) to prevent NOT NULL crashes
                    if "INT" in c_type or "NUM" in c_type: 
                        base_sql = "CAST(row_random(i) * 1000000 AS BIGINT)"
                    else: 
                        base_sql = "uuid()"

            elif is_pk:
                base_sql = f"i + 1 + {{offset}}" if "INT" in c_type else "uuid()"
            else:
                faker_type = None
                upper_name = col_name.upper()
                
                # Check explicit generator type first
                if gen_type == 'datetime' or 'DATE' in c_type or 'TIME' in c_type: 
                    base_sql = "CURRENT_TIMESTAMP - INTERVAL (CAST(row_random(i) * 3650 AS INT)) DAY"
                elif gen_type == 'numerical' or 'FLOAT' in c_type or 'DOUBLE' in c_type or 'DECIMAL' in c_type or 'REAL' in c_type: 
                    base_sql = "CAST(row_random(i) * 10000 AS DOUBLE)"
                elif gen_type == 'integer' or 'INT' in c_type or 'NUM' in c_type: 
                    base_sql = "CAST(row_random(i) * 1000000 AS BIGINT)"
                elif gen_type == 'boolean' or 'BOOL' in c_type: 
                    base_sql = "row_random(i) > 0.5"
                else:
                    # Check explicit allowed_values first
                    use_expanded = bool(col.get('expand_categories', False)) and str(col.get('allowed_values_expanded') or '').strip()
                    allowed_values_str = col.get('allowed_values_expanded') if use_expanded else col.get('allowed_values', '')
                    if allowed_values_str and allowed_values_str.strip():
                        weighted_categories = _parse_weighted_allowed_values(allowed_values_str)
                        categories = [value for value, _ in weighted_categories]
                        if categories:
                            pool_table = f"cat_pool_{table_name}_{col_name}"
                            q_pool_table = _quote_ident(pool_table)
                            weighted_pool = []
                            resolution = 1000
                            for value, weight in weighted_categories:
                                copies = max(1, int(round(weight * resolution)))
                                weighted_pool.extend([value] * copies)
                            pool_values = weighted_pool or categories
                            pool_size = len(pool_values)
                            
                            try:
                                conn.execute(f"CREATE TEMP TABLE {q_pool_table} (id INTEGER, val VARCHAR)")
                                conn.executemany(f"INSERT INTO {q_pool_table} VALUES (?, ?)", list(enumerate(pool_values)))
                                base_sql = f'(SELECT val FROM {q_pool_table} WHERE id = CAST(row_random(i) * {pool_size-1} AS INT) LIMIT 1)'
                            except Exception as e:
                                print(f"TEMP TABLE ERROR: {e}")
                                pass

                    if not base_sql:
                        # Use heuristics for PII strings if Auto / Categorical and no allowed values
                        if "FIRST" in upper_name and "NAME" in upper_name: faker_type = "first_name"
                        elif "LAST" in upper_name and "NAME" in upper_name: faker_type = "last_name"
                        elif "NAME" in upper_name: faker_type = "name"
                        elif "EMAIL" in upper_name: faker_type = "email"
                        elif "PHONE" in upper_name or "CONTACT" in upper_name: faker_type = "phone_number"
                        elif "ADDRESS" in upper_name: faker_type = "address"
                        elif "CITY" in upper_name: faker_type = "city"
                        elif "COUNTRY" in upper_name: faker_type = "country"
                        elif "PIN" in upper_name or "ZIP" in upper_name or "POST" in upper_name: faker_type = "postcode"
                        elif "COMPANY" in upper_name: faker_type = "company"
                        elif "JOB" in upper_name: faker_type = "job"
                        elif "IP" in upper_name: faker_type = "ipv4"
                        
                        # Check for ID columns that aren't primary keys/foreign keys to map to integers
                        if not faker_type and "ID" in upper_name:
                            base_sql = "CAST(row_random(i) * 1000000 AS BIGINT)"
                    
                    if faker_type:
                        pool_table = f"pool_{table_name}_{col_name}"
                        q_pool_table = _quote_ident(pool_table)
                        
                        # Dynamically scale pool size to prevent memory blowout on small jobs
                        pool_size = max(500, min(20000, rows_to_gen // 2))
                        
                        try:
                            vals = generate_pool(faker_type, pool_size, current_seed + hash(col_name))
                            
                            conn.execute(f"CREATE TEMP TABLE {q_pool_table} (id INTEGER, val VARCHAR)")
                            # Ingest pool without giant string concatenation
                            conn.executemany(f"INSERT INTO {q_pool_table} VALUES (?, ?)", list(enumerate(vals)))
                            
                            base_sql = f'(SELECT val FROM {q_pool_table} WHERE id = CAST(row_random(i) * {pool_size-1} AS INT) LIMIT 1)'
                        except AttributeError:
                            base_sql = "'val_' || CAST(row_random(i) * 1000 AS INT)"
                    
                    if not base_sql:
                        base_sql = "'val_' || CAST(row_random(i) * 1000 AS INT)"
            
            # Apply NULLs if column is nullable and not a Primary Key
            if is_nullable and not is_pk and not fk_rel:
                # 10% chance of NULL
                sql_cols.append(f"CASE WHEN row_random(i) < 0.10 THEN NULL ELSE {base_sql} END")
            else:
                sql_cols.append(base_sql)

        # 3. Chunked Execution
        #actual size - 100000, changing it
        CHUNK_SIZE = 10000
        for chunk_offset in range(0, rows_to_gen, CHUNK_SIZE):
            count = min(CHUNK_SIZE, rows_to_gen - chunk_offset)
            # We format the string to inject the correct offset for numerical PKs
            current_cols = [c.format(offset=chunk_offset) for c in sql_cols]
            conn.execute(f"INSERT INTO {q_table_name} SELECT {', '.join(current_cols)} FROM range({count}) t(i)")

        _apply_product_price_consistency(conn, table_name, table_cols)

    return generation_order
