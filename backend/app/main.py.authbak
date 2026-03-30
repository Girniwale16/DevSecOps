import os
import duckdb
import shutil
import uuid
import json
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Body
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import zipfile
import io
import time
import re
from typing import Any, Dict, List, Optional

from app.engine.profiler import get_csv_stats
from app.engine.synthesizer import generate_synthetic_data
from app.engine.metadata import init_db, get_db_connection
from app.engine.schema_parser import parse_ddl, normalize_schema
from app.engine.mock_generator import generate_mock_from_schema
from app.engine.multi_table_synthesizer import generate_multi_table_data, generate_multi_table_data_fast
from app.engine.graph_builder import build_schema_graph, get_topological_sort
from app.engine.relational_planner import RelationalPlanner
from app.engine.semantic_inference import infer_column_semantics
from app.engine.project_summary import infer_project_summary
from app.engine.pii_detector import detect_pii_columns
from app.engine.relationship_inference import infer_table_relationships
from app.engine.category_expansion import expand_categorical_column, parse_allowed_values
from app.engine.association_inference import infer_column_associations
from app.engine.assistant_chat import infer_assistant_reply
import pandas as pd
import numpy as np

def safe_df_to_dict(df):
    """Replaces NaN/Inf with None so it's JSON compliant."""
    return df.replace({np.nan: None, np.inf: None, -np.inf: None}).to_dict('records')


def _normalize_table_generation_settings(
    raw_settings: Optional[Dict[str, Any]],
    table_names: List[str],
    default_rows: int,
    default_seed: int,
) -> Dict[str, Dict[str, int]]:
    normalized: Dict[str, Dict[str, int]] = {}
    incoming = raw_settings if isinstance(raw_settings, dict) else {}
    valid_names = {str(name) for name in table_names if str(name).strip()}
    for table_name in valid_names:
        cfg = incoming.get(table_name) or {}
        try:
            rows = int(float(cfg.get("num_rows", default_rows)))
        except Exception:
            rows = int(default_rows)
        try:
            seed = int(float(cfg.get("seed", default_seed)))
        except Exception:
            seed = int(default_seed)
        normalized[table_name] = {
            "num_rows": max(1, rows),
            "seed": seed,
        }
    return normalized


def _resize_dataframe_to_count(df: pd.DataFrame, target_rows: int, seed: int) -> pd.DataFrame:
    target = max(1, int(target_rows))
    if len(df) == target:
        return df.reset_index(drop=True)
    if df.empty:
        return df
    if len(df) > target:
        return df.sample(n=target, random_state=int(seed)).reset_index(drop=True)
    extra = df.sample(n=target - len(df), replace=True, random_state=int(seed) + 1)
    return pd.concat([df, extra], ignore_index=True).reset_index(drop=True)


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _infer_generator_from_dtype(data_type: str) -> str:
    dtype = str(data_type or "").upper()
    if any(t in dtype for t in ["DATE", "TIME"]):
        return "datetime"
    if any(t in dtype for t in ["INT", "BIGINT", "SMALLINT"]):
        return "integer"
    if any(t in dtype for t in ["NUM", "DEC", "DOUBLE", "FLOAT", "REAL"]):
        return "numerical"
    return "categorical"


def _normalize_allowed_values(raw: object) -> str:
    if isinstance(raw, list):
        values = [str(v).strip() for v in raw if str(v).strip()]
        return ", ".join(dict.fromkeys(values))

    text = str(raw or "").strip()
    if not text:
        return ""

    parts = [p.strip() for p in re.split(r"[\n,;]+", text) if p.strip()]
    return ", ".join(dict.fromkeys(parts))


def _parse_weighted_allowed_values(raw: object) -> List[tuple[str, float]]:
    text = str(raw or "").strip()
    if not text:
        return []

    entries = []
    seen = set()
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
        if not value or value in seen:
            continue
        seen.add(value)
        entries.append((value, weight))

    if not entries:
        return []
    total = sum(weight for _, weight in entries)
    if total <= 0:
        uniform = 1.0 / len(entries)
        return [(value, uniform) for value, _ in entries]
    return [(value, weight / total) for value, weight in entries]


def _resolve_allowed_values(cfg: Dict[str, Any]) -> str:
    expand_categories = bool(cfg.get("expand_categories", False))
    manual = _normalize_allowed_values(cfg.get("allowed_values"))
    expanded = _normalize_allowed_values(cfg.get("allowed_values_expanded"))
    if expand_categories and expanded:
        return expanded
    if manual:
        return manual
    if expanded:
        return expanded
    return ""


def _default_categorical_pool(column_name: str, size: int = 12) -> List[str]:
    base = re.sub(r"[^a-z0-9]+", "_", str(column_name or "value").strip().lower()).strip("_") or "value"
    return [f"{base}_{idx}" for idx in range(1, max(2, int(size)) + 1)]


def _build_key_column_set(relations: List[Dict[str, Any]]) -> set:
    locked = set()
    for rel in relations or []:
        ft = str(rel.get("from_table") or "")
        fc = str(rel.get("from_column") or "")
        tt = str(rel.get("to_table") or "")
        tc = str(rel.get("to_column") or "")
        if ft and fc:
            locked.add((ft, fc))
        if tt and tc:
            locked.add((tt, tc))
    return locked


def _anonymize_series(col_name: str, series: pd.Series, rng: np.random.Generator) -> pd.Series:
    lname = str(col_name).lower()
    out = series.copy()
    mask = out.notna()
    count = int(mask.sum())
    if count <= 0:
        return out

    if "email" in lname:
        vals = [f"user{int(rng.integers(100000, 999999))}@example.com" for _ in range(count)]
    elif any(k in lname for k in ["phone", "mobile", "contact"]):
        vals = [f"+1{int(rng.integers(2000000000, 9999999999))}" for _ in range(count)]
    elif "name" in lname:
        vals = [f"Person_{int(rng.integers(10000, 99999))}" for _ in range(count)]
    elif any(k in lname for k in ["address", "street"]):
        vals = [f"Address_{int(rng.integers(10000, 99999))}" for _ in range(count)]
    else:
        vals = [f"masked_{int(rng.integers(100000, 999999))}" for _ in range(count)]

    out.loc[mask] = vals
    return out


def _randomize_series(
    series: pd.Series,
    generator_type: str,
    pct: float,
    data_type: str,
    rng: np.random.Generator,
    allowed_values: str = "",
) -> pd.Series:
    out = series.copy()
    mask = out.notna()
    idx = np.where(mask.values)[0]
    if len(idx) == 0:
        return out

    frac = max(0.0, min(100.0, float(pct or 0.0))) / 100.0
    if frac <= 0:
        return out
    sample_size = max(1, int(round(len(idx) * frac)))
    chosen = rng.choice(idx, size=min(sample_size, len(idx)), replace=False)

    g = str(generator_type or "auto").lower()
    if g == "auto":
        g = _infer_generator_from_dtype(data_type)

    if allowed_values and str(allowed_values).strip():
        weighted = _parse_weighted_allowed_values(allowed_values)
        values = [value for value, _ in weighted]
        probs = [weight for _, weight in weighted] if weighted else None
        upper_type = str(data_type or "").upper()
        if values:
            if any(token in upper_type for token in ["INT", "NUM", "DEC", "DOUBLE", "FLOAT", "REAL"]):
                parsed_numeric = pd.to_numeric(pd.Series(values), errors="coerce")
                if parsed_numeric.notna().all():
                    numeric_values = parsed_numeric.astype(float).to_numpy()
                    numeric_out = pd.to_numeric(out, errors="coerce").astype(float)
                    sampled_numeric = rng.choice(numeric_values, size=len(chosen), replace=True, p=probs)
                    numeric_out.iloc[chosen] = sampled_numeric
                    if "INT" in upper_type:
                        numeric_out = numeric_out.round().astype("Int64")
                    return numeric_out
            if any(token in upper_type for token in ["DATE", "TIME"]):
                parsed_dt = pd.to_datetime(pd.Series(values), errors="coerce")
                if parsed_dt.notna().all():
                    datetime_out = pd.to_datetime(out, errors="coerce")
                    sampled_dt = rng.choice(parsed_dt.dt.to_pydatetime(), size=len(chosen), replace=True, p=probs)
                    datetime_out.iloc[chosen] = pd.to_datetime(sampled_dt)
                    return datetime_out
        object_out = out.astype("object")
        object_out.iloc[chosen] = rng.choice(values, size=len(chosen), replace=True, p=probs)
        return object_out

    if g == "numerical":
        numeric = pd.to_numeric(out, errors="coerce").astype(float)
        valid = numeric.dropna()
        if valid.empty:
            new_vals = rng.integers(0, 1000000, size=len(chosen))
        else:
            low = float(valid.min())
            high = float(valid.max())
            if np.isclose(low, high):
                high = low + 1.0
            new_vals = rng.uniform(low, high, size=len(chosen))
            if str(data_type).upper().find("INT") >= 0:
                new_vals = np.round(new_vals).astype("int64")
        out.iloc[chosen] = new_vals
        return out

    if g == "datetime":
        dt = pd.to_datetime(out, errors="coerce")
        valid = dt.dropna()
        if valid.empty:
            end = pd.Timestamp.utcnow().normalize()
            start = end - pd.Timedelta(days=3650)
        else:
            start = valid.min()
            end = valid.max()
            if pd.isna(start) or pd.isna(end) or start == end:
                end = pd.Timestamp.utcnow().normalize()
                start = end - pd.Timedelta(days=3650)
        span_days = max(1, int((end - start).days))
        offsets = rng.integers(0, span_days + 1, size=len(chosen))
        out.iloc[chosen] = [start + pd.Timedelta(days=int(v)) for v in offsets]
        return out

    # categorical/default
    values = []
    probs = None
    if not values:
        values = _default_categorical_pool(str(series.name or "value"), size=10)
    object_out = out.astype("object")
    object_out.iloc[chosen] = rng.choice(values, size=len(chosen), replace=True, p=probs)
    return object_out


def _infer_numeric_columns(df: pd.DataFrame, column_configs: List[Dict[str, Any]]) -> List[str]:
    config_by_col = {str(cfg.get("column_name") or ""): cfg for cfg in column_configs or []}
    numeric_cols: List[str] = []
    for col in df.columns:
        cfg = config_by_col.get(str(col), {})
        dtype = str(cfg.get("data_type") or df[col].dtype or "")
        gen_type = str(cfg.get("generator_type") or "").strip().lower()
        upper_dtype = dtype.upper()
        if gen_type in {"integer", "numerical"} or any(t in upper_dtype for t in ["INT", "NUM", "DEC", "DOUBLE", "FLOAT", "REAL"]):
            numeric_series = pd.to_numeric(df[col], errors="coerce")
            if numeric_series.notna().any():
                numeric_cols.append(str(col))
    return numeric_cols


def _apply_numeric_variation(
    df: pd.DataFrame,
    numeric_cols: List[str],
    seed: int,
    stddev_scale: float,
    variation_pct: float,
) -> pd.DataFrame:
    if df is None or df.empty or not numeric_cols:
        return df
    if stddev_scale <= 0 and variation_pct <= 0:
        return df

    out = df.copy()
    rng = np.random.default_rng(int(seed) + 8801)
    variation_factor = max(0.0, float(variation_pct)) / 100.0
    std_scale = max(0.0, float(stddev_scale))

    for idx, col in enumerate(numeric_cols):
        series = pd.to_numeric(out[col], errors="coerce").astype(float)
        valid = series.dropna()
        if valid.empty:
            continue
        observed_std = float(valid.std(ddof=0)) if len(valid) > 1 else 0.0
        value_range = float(valid.max() - valid.min())
        base_std = observed_std if observed_std > 0 else max(value_range * 0.1, 1.0)
        noise_std = base_std * std_scale * variation_factor
        if noise_std <= 0:
            continue
        local_rng = np.random.default_rng(int(seed) + 8819 + idx)
        noise = local_rng.normal(0.0, noise_std, size=len(out))
        adjusted = series.copy()
        adjusted.loc[series.notna()] = series.loc[series.notna()] + noise[series.notna().to_numpy()]

        dtype_hint = str((out[col].dtype if col in out.columns else "")).lower()
        if "int" in dtype_hint:
            adjusted = np.round(adjusted).astype("Int64")
        out[col] = adjusted
    return out


def _apply_knn_smoothing(
    df: pd.DataFrame,
    source_df: pd.DataFrame,
    numeric_cols: List[str],
    seed: int,
    smoothing: float,
    neighbors: int,
) -> pd.DataFrame:
    if df is None or df.empty or source_df is None or source_df.empty or not numeric_cols:
        return df
    alpha = min(1.0, max(0.0, float(smoothing)))
    if alpha <= 0:
        return df

    usable_cols = []
    for col in numeric_cols:
        if col not in source_df.columns:
            continue
        src_numeric = pd.to_numeric(source_df[col], errors="coerce")
        syn_numeric = pd.to_numeric(df[col], errors="coerce")
        if src_numeric.notna().any() and syn_numeric.notna().any():
            usable_cols.append(col)
    if not usable_cols:
        return df

    src_numeric_df = source_df[usable_cols].apply(pd.to_numeric, errors="coerce").dropna()
    if src_numeric_df.empty:
        return df

    sample_cap = min(len(src_numeric_df), 5000)
    src_sample = src_numeric_df.sample(n=sample_cap, random_state=int(seed)) if len(src_numeric_df) > sample_cap else src_numeric_df
    src_matrix = src_sample.to_numpy(dtype=float)
    means = src_matrix.mean(axis=0)
    stds = src_matrix.std(axis=0)
    stds[stds == 0] = 1.0
    src_norm = (src_matrix - means) / stds
    k = max(1, min(int(neighbors), len(src_sample)))

    out = df.copy()
    synth_numeric = out[usable_cols].apply(pd.to_numeric, errors="coerce")
    for row_idx in range(len(out)):
        row = synth_numeric.iloc[row_idx]
        if row.isna().any():
            continue
        row_vec = row.to_numpy(dtype=float)
        row_norm = (row_vec - means) / stds
        distances = np.linalg.norm(src_norm - row_norm, axis=1)
        nearest_idx = np.argpartition(distances, k - 1)[:k]
        neighbor_mean = src_matrix[nearest_idx].mean(axis=0)
        blended = ((1.0 - alpha) * row_vec) + (alpha * neighbor_mean)
        for col_pos, col_name in enumerate(usable_cols):
            current_series = pd.to_numeric(out[col_name], errors="coerce")
            if "int" in str(out[col_name].dtype).lower():
                out.at[out.index[row_idx], col_name] = int(round(blended[col_pos]))
            else:
                out.at[out.index[row_idx], col_name] = float(blended[col_pos])
    return out


def _apply_generation_parameters(
    table_name: str,
    df: pd.DataFrame,
    column_configs: List[Dict[str, Any]],
    seed: int,
    generation_params: Dict[str, Any],
    source_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    stddev_scale = float(generation_params.get("stddev_scale", 1.0) or 1.0)
    variation_pct = float(generation_params.get("variation_pct", 0.0) or 0.0)
    knn_smoothing = float(generation_params.get("knn_smoothing", 0.0) or 0.0)
    knn_neighbors = int(generation_params.get("knn_neighbors", 5) or 5)

    numeric_cols = _infer_numeric_columns(df, column_configs)
    out = _apply_numeric_variation(df, numeric_cols, seed, stddev_scale, variation_pct)
    if source_df is not None and knn_smoothing > 0:
        out = _apply_knn_smoothing(out, source_df, numeric_cols, seed + 101, knn_smoothing, knn_neighbors)
    return out


def _apply_modeling_config_to_df(
    table_name: str,
    df: pd.DataFrame,
    column_configs: List[Dict[str, Any]],
    key_columns: set,
    seed: int,
) -> pd.DataFrame:
    if df is None or df.empty or not column_configs:
        return df

    out = df.copy()
    for i, cfg in enumerate(column_configs):
        col_name = str(cfg.get("column_name") or "")
        if not col_name or col_name not in out.columns:
            continue
        if (str(table_name), col_name) in key_columns or bool(cfg.get("is_pk", False)):
            continue

        rng = np.random.default_rng(int(seed) + i + 17)
        gen_type = str(cfg.get("generator_type") or "auto").strip().lower()
        data_type = str(cfg.get("data_type") or "")
        rand_pct = float(cfg.get("randomization_pct") or 0.0)
        pii = bool(cfg.get("is_pii", False))
        resolved_allowed_values = _resolve_allowed_values(cfg)
        null_pct = cfg.get("null_value_percent")
        min_val = cfg.get("min_val")
        max_val = cfg.get("max_val")

        effective_gen_type = gen_type if gen_type != "auto" else _infer_generator_from_dtype(data_type)

        if pii:
            out[col_name] = _anonymize_series(col_name, out[col_name], rng)
        if resolved_allowed_values:
            out[col_name] = _randomize_series(
                out[col_name],
                effective_gen_type,
                100.0,
                data_type,
                rng,
                resolved_allowed_values,
            )
        if rand_pct > 0:
            out[col_name] = _randomize_series(out[col_name], effective_gen_type, rand_pct, data_type, rng, resolved_allowed_values)
        numeric_series = pd.to_numeric(out[col_name], errors="coerce").astype(float)
        min_num = None if min_val in (None, "") else pd.to_numeric(pd.Series([min_val]), errors="coerce").iloc[0]
        max_num = None if max_val in (None, "") else pd.to_numeric(pd.Series([max_val]), errors="coerce").iloc[0]
        should_apply_numeric_bounds = (
            numeric_series.notna().any()
            and (pd.notna(min_num) or pd.notna(max_num))
        )
        if should_apply_numeric_bounds:
            if pd.notna(min_num) and pd.notna(max_num) and float(min_num) > float(max_num):
                min_num, max_num = max_num, min_num
            valid_mask = numeric_series.notna()
            if pd.notna(min_num) and pd.notna(max_num):
                lower = float(min_num)
                upper = float(max_num)
                if np.isclose(lower, upper):
                    numeric_series.loc[valid_mask] = lower
                else:
                    sampled = rng.uniform(lower, upper, size=int(valid_mask.sum()))
                    numeric_series.loc[valid_mask] = sampled
            else:
                if pd.notna(min_num):
                    numeric_series = numeric_series.clip(lower=float(min_num))
                if pd.notna(max_num):
                    numeric_series = numeric_series.clip(upper=float(max_num))
            if effective_gen_type == "integer" or "INT" in str(data_type or "").upper():
                numeric_series = numeric_series.round().astype("Int64")
            out[col_name] = numeric_series
        if null_pct is not None and str(null_pct).strip():
            try:
                desired_null_fraction = max(0.0, min(100.0, float(null_pct))) / 100.0
                desired_nulls = int(round(len(out) * desired_null_fraction))
                if desired_nulls > 0:
                    null_indices = rng.choice(out.index.to_numpy(), size=min(desired_nulls, len(out)), replace=False)
                    out.loc[null_indices, col_name] = pd.NA
            except Exception:
                pass

    return out


def _load_synth_tables_from_output(output_path: str) -> Dict[str, pd.DataFrame]:
    tables: Dict[str, pd.DataFrame] = {}
    lower = str(output_path).lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(output_path, "r") as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                ext = os.path.splitext(member)[1].lower()
                table_name = os.path.splitext(os.path.basename(member))[0]
                data = zf.read(member)
                buf = io.BytesIO(data)
                if ext == ".csv":
                    tables[table_name] = pd.read_csv(buf)
                elif ext == ".parquet":
                    tables[table_name] = pd.read_parquet(buf)
        return tables

    name = os.path.splitext(os.path.basename(output_path))[0]
    if lower.endswith(".csv"):
        tables[name] = pd.read_csv(output_path)
    elif lower.endswith(".parquet"):
        tables[name] = pd.read_parquet(output_path)
    return tables

# Task tracking
tasks = {} # task_id -> { status: 'running'|'done'|'failed', progress: 0-100, logs: [], file_path: str }

app = FastAPI(title="DataCosmos API")

# Initialize metadata DB on startup
@app.on_event("startup")
async def startup_event():
    init_db()

# Enable CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

UPLOAD_DIR = "data/uploads"
EXPORT_DIR = "data/exports"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

@app.get("/")
async def root():
    return {"message": "DataCosmos API is running"}

@app.post("/upload")
async def upload_csv(file: UploadFile = File(...)):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed")
    
    project_id = str(uuid.uuid4())
    table_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{table_id}.csv")
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        stats = get_csv_stats(file_path)
        initial_table_name = os.path.splitext(file.filename)[0].strip() or "table_1"
        
        conn = get_db_connection()
        # 1. Create Project
        conn.execute("INSERT INTO projects (id, name, source_type) VALUES (?, ?, ?)",
                     (project_id, file.filename, 'CSV'))
        
        # 2. Create Table
        conn.execute("INSERT INTO tables (id, project_id, name, file_path, row_count) VALUES (?, ?, ?, ?, ?)",
                     (table_id, project_id, initial_table_name, file_path, stats["total_rows"]))
        
        # 3. Create Columns and Profiles
        for col in stats["columns"]:
            col_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO columns (id, table_id, name, data_type) 
                VALUES (?, ?, ?, ?)
            """, (col_id, table_id, col["column"], col["type"]))
            
            conn.execute("""
                INSERT INTO column_profiles (id, column_id, null_count, min_val, max_val, cardinality, sd, variance, null_value_percent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(uuid.uuid4()), col_id, col["nulls"], col["min"], col["max"], col["cardinality"], col.get("sd"), col.get("variance"), col.get("null_value_percent")))
            
        conn.close()

        return {
            "project_id": project_id,
            "table_id": table_id,
            "filename": file.filename,
            "stats": stats
        }
    except Exception as e:
        if os.path.exists(file_path): os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Error processing CSV: {str(e)}")

@app.post("/upload-ddl")
async def upload_ddl_api(file: UploadFile = File(...), dialect: str = "postgres"):
    if not file.filename.endswith('.sql'):
        raise HTTPException(status_code=400, detail="Only SQL files are allowed")
    
    content = (await file.read()).decode("utf-8")
    project_id = str(uuid.uuid4())
    
    try:
        tables = parse_ddl(content, dialect=dialect)
        conn = get_db_connection()
        
        # 1. Create Project
        conn.execute("INSERT INTO projects (id, name, source_type) VALUES (?, ?, ?)",
                     (project_id, file.filename, 'DDL'))
        
        for table in tables:
            table_id = str(uuid.uuid4())
            conn.execute("INSERT INTO tables (id, project_id, name) VALUES (?, ?, ?)",
                         (table_id, project_id, table["table_name"]))
            
            for col in table["columns"]:
                is_pk = col["name"] in table["primary_keys"]
                conn.execute("""
                    INSERT INTO columns (id, table_id, name, data_type, is_pk, is_nullable)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (str(uuid.uuid4()), table_id, col["name"], col["type"], is_pk, col["is_nullable"]))
            
            for fk in table["foreign_keys"]:
                print(f"DEBUG: Found FK in {table['table_name']}: {fk}")
                for i, from_col in enumerate(fk["columns"]):
                    to_col = fk["ref_columns"][i]
                    # Detect optionality from column nullability
                    col_info = next((c for c in table["columns"] if c["name"] == from_col), {})
                    is_optional = col_info.get("is_nullable", True)
                    
                    conn.execute("""
                        INSERT INTO relations (id, project_id, from_table, from_column, to_table, to_column, cardinality, is_optional)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (str(uuid.uuid4()), project_id, table["table_name"], from_col, fk["ref_table"], to_col, '1:N', is_optional))
        
        conn.close()
        return {"project_id": project_id, "tables": tables}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DDL Parsing failed: {str(e)}")

@app.post("/upload-schema")
async def upload_schema(payload = Body(...)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Invalid payload. Expected JSON object.")

    tables = payload.get("tables", [])
    if not isinstance(tables, list) or not tables:
        raise HTTPException(status_code=400, detail="At least one table is required.")

    project_name = str(payload.get("project_name") or "DataCosmos").strip() or "DataCosmos"
    project_id = str(uuid.uuid4())
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO projects (id, name, source_type) VALUES (?, ?, ?)",
            (project_id, project_name, "SCHEMA"),
        )

        normalized_tables = []
        for t_idx, table in enumerate(tables, start=1):
            if not isinstance(table, dict):
                raise HTTPException(status_code=400, detail=f"Table {t_idx}: invalid table object.")

            table_name = str(table.get("table_name") or table.get("name") or "").strip()
            if not table_name:
                raise HTTPException(status_code=400, detail=f"Table {t_idx}: table_name is required.")

            columns = table.get("columns", [])
            if not isinstance(columns, list) or not columns:
                raise HTTPException(status_code=400, detail=f"Table '{table_name}': at least one column is required.")

            table_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO tables (id, project_id, name) VALUES (?, ?, ?)",
                (table_id, project_id, table_name),
            )

            normalized_columns = []
            for c_idx, col in enumerate(columns, start=1):
                if not isinstance(col, dict):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Table '{table_name}', column {c_idx}: invalid column object.",
                    )

                col_name = str(col.get("name") or col.get("column_name") or "").strip()
                if not col_name:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Table '{table_name}', column {c_idx}: column name is required.",
                    )

                data_type = str(col.get("data_type") or col.get("type") or "varchar").strip() or "varchar"
                desc = str(col.get("description") or "").strip()
                generator_type = str(col.get("generator_type") or "").strip().lower() or _infer_generator_from_dtype(data_type)
                if generator_type not in {"auto", "categorical", "integer", "numerical", "datetime"}:
                    generator_type = _infer_generator_from_dtype(data_type)

                mandatory_raw = col.get("mandatory", False)
                if isinstance(mandatory_raw, str):
                    mandatory = mandatory_raw.strip().lower() in {"yes", "true", "1", "y"}
                else:
                    mandatory = bool(mandatory_raw)
                is_nullable = not mandatory
                allowed_values = _normalize_allowed_values(col.get("allowed_values"))

                conn.execute(
                    """
                    INSERT INTO columns (id, table_id, name, data_type, is_nullable, generator_type, allowed_values, expand_categories)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        table_id,
                        col_name,
                        data_type,
                        is_nullable,
                        generator_type,
                        allowed_values,
                        bool(col.get("expand_categories", False)),
                    ),
                )
                normalized_columns.append(
                    {
                        "name": col_name,
                        "data_type": data_type,
                        "description": desc,
                        "mandatory": mandatory,
                    }
                )

            normalized_tables.append(
                {
                    "table_name": table_name,
                    "description": str(table.get("description") or "").strip(),
                    "columns": normalized_columns,
                }
            )

        return {
            "project_id": project_id,
            "project_name": project_name,
            "tables": normalized_tables,
        }
    except HTTPException:
        # cleanup partial project writes
        try:
            conn.execute(
                """
                DELETE FROM column_profiles
                WHERE column_id IN (
                    SELECT c.id
                    FROM columns c
                    JOIN tables t ON c.table_id = t.id
                    WHERE t.project_id = CAST(? AS UUID)
                )
                """,
                (project_id,),
            )
            conn.execute(
                """
                DELETE FROM columns
                WHERE table_id IN (
                    SELECT id FROM tables WHERE project_id = CAST(? AS UUID)
                )
                """,
                (project_id,),
            )
            conn.execute("DELETE FROM relations WHERE project_id = CAST(? AS UUID)", (project_id,))
            conn.execute("DELETE FROM tables WHERE project_id = CAST(? AS UUID)", (project_id,))
            conn.execute("DELETE FROM projects WHERE id = CAST(? AS UUID)", (project_id,))
        except Exception:
            pass
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema upload failed: {str(e)}")
    finally:
        conn.close()

@app.get("/project/{project_id}")
async def get_project(project_id: str):
    conn = get_db_connection()
    try:
        project = safe_df_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).df())[0]
        tables = safe_df_to_dict(conn.execute("SELECT * FROM tables WHERE project_id = ?", (project_id,)).df())
        
        for table in tables:
            table_id = table['id']
            # Get columns joined with profiles if they exist
            cols = safe_df_to_dict(conn.execute("""
                SELECT c.*, p.null_count, p.min_val, p.max_val, p.cardinality, p.sd, p.variance, p.null_value_percent 
                FROM columns c
                LEFT JOIN column_profiles p ON c.id = p.column_id
                WHERE c.table_id = ?
            """, (table_id,)).df())
            table['columns'] = cols
            
        relations = safe_df_to_dict(conn.execute("SELECT * FROM relations WHERE project_id = ?", (project_id,)).df())
        
        return {
            "project": project,
            "tables": tables,
            "relations": relations
        }
    finally:
        conn.close()


@app.get("/project/{project_id}/tables/{table_id}/correlations")
async def get_table_correlations(project_id: str, table_id: str, top_k: int = 30):
    conn = get_db_connection()
    try:
        table_rows = safe_df_to_dict(
            conn.execute(
                "SELECT id, name, file_path FROM tables WHERE id = ? AND project_id = CAST(? AS UUID)",
                (table_id, project_id),
            ).df()
        )
        if not table_rows:
            raise HTTPException(status_code=404, detail="Table not found")

        file_path = table_rows[0].get("file_path")
        if not file_path or not os.path.exists(file_path):
            return {"table_id": table_id, "correlations": [], "note": "No source data available."}

        try:
            df = pd.read_csv(file_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to read source data: {exc}")

        if df.empty:
            return {"table_id": table_id, "correlations": [], "note": "No rows available for correlation."}

        numeric_df = df.select_dtypes(include=["number"])
        if numeric_df.shape[1] < 2:
            corr_rows = []
            corr_note = "Need at least two numeric columns."
        else:
            corr = numeric_df.corr().fillna(0.0)
            corr_rows = []
            cols = list(corr.columns)
            for i in range(len(cols)):
                for j in range(i + 1, len(cols)):
                    corr_rows.append({"col_a": cols[i], "col_b": cols[j], "corr": float(corr.iloc[i, j])})
            corr_rows.sort(key=lambda r: abs(r["corr"]), reverse=True)
            if top_k is not None and int(top_k) > 0:
                corr_rows = corr_rows[: int(top_k)]
            corr_note = None

        assoc_rows = []
        assoc_note = None
        cat_df = df.select_dtypes(exclude=["number"]).copy()
        if cat_df.shape[1] >= 2:
            max_unique = 50
            for col in list(cat_df.columns):
                uniques = cat_df[col].dropna().unique()
                if len(uniques) > max_unique:
                    cat_df = cat_df.drop(columns=[col])
            cat_cols = list(cat_df.columns)
            if len(cat_cols) < 2:
                assoc_note = "Categorical columns have too many unique values."
            else:
                for i in range(len(cat_cols)):
                    for j in range(i + 1, len(cat_cols)):
                        c1 = cat_cols[i]
                        c2 = cat_cols[j]
                        if cat_df[c1].dropna().nunique() < 2 or cat_df[c2].dropna().nunique() < 2:
                            continue
                        ct = pd.crosstab(cat_df[c1], cat_df[c2])
                        if ct.size == 0:
                            continue
                        obs = ct.to_numpy()
                        n = obs.sum()
                        if n == 0:
                            continue
                        row_sums = obs.sum(axis=1, keepdims=True)
                        col_sums = obs.sum(axis=0, keepdims=True)
                        expected = row_sums @ col_sums / n
                        with np.errstate(divide="ignore", invalid="ignore"):
                            chi2 = np.nansum((obs - expected) ** 2 / expected)
                        r, k = obs.shape
                        denom = n * (min(r - 1, k - 1))
                        if denom <= 0:
                            continue
                        cramers_v = float(np.sqrt(chi2 / denom))
                        assoc_rows.append({"col_a": c1, "col_b": c2, "score": cramers_v, "metric": "Cramer's V"})
                assoc_rows.sort(key=lambda r: r["score"], reverse=True)
                if top_k is not None and int(top_k) > 0:
                    assoc_rows = assoc_rows[: int(top_k)]
        else:
            assoc_note = "Need at least two categorical columns."

        llm_rows = []
        llm_note = None
        llm_source = None
        llm_model = None
        try:
            sample_df = df.head(500)
            columns_payload = []
            for col_name in sample_df.columns:
                series = sample_df[col_name]
                data_type = str(series.dtype)
                cardinality = int(series.nunique(dropna=True))
                sample_values = (
                    series.dropna()
                    .astype(str)
                    .value_counts()
                    .head(6)
                    .index.tolist()
                )
                columns_payload.append(
                    {
                        "column_name": col_name,
                        "data_type": data_type,
                        "cardinality": cardinality,
                        "sample_values": sample_values,
                    }
                )
            llm_resp = await infer_column_associations(table_rows[0].get("name", ""), columns_payload)
            llm_rows = llm_resp.get("associations", [])
            llm_source = llm_resp.get("source")
            llm_model = llm_resp.get("model")
            if llm_resp.get("error"):
                llm_note = llm_resp.get("error")
        except Exception as exc:
            llm_note = f"LLM association failed: {exc}"

        return {
            "table_id": table_id,
            "correlations": corr_rows,
            "note": corr_note,
            "associations": assoc_rows,
            "assoc_note": assoc_note,
            "llm_associations": llm_rows,
            "llm_note": llm_note,
            "llm_source": llm_source,
            "llm_model": llm_model,
        }
    finally:
        conn.close()


@app.get("/project/{project_id}/summary")
async def get_project_natural_summary(project_id: str):
    conn = get_db_connection()
    try:
        project_rows = safe_df_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).df())
        if not project_rows:
            raise HTTPException(status_code=404, detail="Project not found")

        project = project_rows[0]
        tables = safe_df_to_dict(conn.execute("SELECT * FROM tables WHERE project_id = ?", (project_id,)).df())

        for table in tables:
            table_id = table["id"]
            cols = safe_df_to_dict(
                conn.execute(
                    """
                    SELECT c.id, c.name, c.data_type, c.is_pk, c.is_pii
                    FROM columns c
                    WHERE c.table_id = ?
                    """,
                    (table_id,),
                ).df()
            )
            table["columns"] = cols

        relations = safe_df_to_dict(conn.execute("SELECT * FROM relations WHERE project_id = ?", (project_id,)).df())

        summary = await infer_project_summary(
            jsonable_encoder(project),
            jsonable_encoder(tables),
            jsonable_encoder(relations),
        )

        return jsonable_encoder(
            {
                "project_id": project_id,
                "summary": summary.get("summary", ""),
                "source": summary.get("source"),
                "model": summary.get("model"),
                "error": summary.get("error"),
            }
        )
    finally:
        conn.close()


@app.post("/assistant/chat")
async def assistant_chat(payload=Body(...)):
    current_page = str((payload or {}).get("page") or "upload").strip().lower()
    setup_mode = str((payload or {}).get("setup_mode") or "").strip().lower()
    project_id = str((payload or {}).get("project_id") or "").strip()
    message = str((payload or {}).get("message") or "").strip()
    history = (payload or {}).get("history") or []

    if not message:
        raise HTTPException(status_code=422, detail="Message is required")

    project = None
    tables: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []

    if project_id:
        conn = get_db_connection()
        try:
            project_rows = safe_df_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).df())
            if project_rows:
                project = project_rows[0]
                tables = safe_df_to_dict(conn.execute("SELECT * FROM tables WHERE project_id = ?", (project_id,)).df())
                for table in tables:
                    table_id = table["id"]
                    table["columns"] = safe_df_to_dict(
                        conn.execute(
                            """
                            SELECT c.id, c.name, c.data_type, c.is_pk, c.is_pii
                            FROM columns c
                            WHERE c.table_id = ?
                            """,
                            (table_id,),
                        ).df()
                    )
                relations = safe_df_to_dict(conn.execute("SELECT * FROM relations WHERE project_id = ?", (project_id,)).df())
        finally:
            conn.close()

    reply = await infer_assistant_reply(
        current_page=current_page,
        setup_mode=setup_mode,
        has_project=bool(project),
        project=project,
        tables=tables,
        relations=relations,
        history=history if isinstance(history, list) else [],
        message=message,
    )
    return jsonable_encoder(reply)


@app.post("/project/{project_id}/config/update")
async def update_project_config(project_id: str, payload = Body(...)):
    """
    Accepts either:
      1) raw list: [ {id, is_pii, generator_type, randomization_pct}, ... ]
      2) wrapped object: { "config": [ ... ] }
    """
    if isinstance(payload, dict):
        config = payload.get("config")
    else:
        config = payload

    if not isinstance(config, list):
        raise HTTPException(
            status_code=422,
            detail="Invalid payload. Expected a JSON list or an object with 'config' list.",
        )

    conn = get_db_connection()
    try:
        updated_count = 0
        for col in config:
            if not isinstance(col, dict) or "id" not in col:
                continue
            col_id = str(col["id"])
            new_allowed_values = _normalize_allowed_values(col.get("allowed_values"))
            new_allowed_values_expanded = _normalize_allowed_values(col.get("allowed_values_expanded"))
            expand_categories = bool(col.get("expand_categories", False))
            existing_rows = conn.execute(
                "SELECT allowed_values FROM columns WHERE id = CAST(? AS UUID)",
                (col_id,),
            ).df().to_dict("records")
            existing_allowed_values = _normalize_allowed_values(existing_rows[0].get("allowed_values")) if existing_rows else ""
            if new_allowed_values != existing_allowed_values:
                new_allowed_values_expanded = ""
            conn.execute("""
                UPDATE columns 
                SET is_pii = ?, generator_type = ?, data_type = ?, randomization_pct = ?, allowed_values = ?, allowed_values_expanded = ?, expand_categories = ?
                WHERE id = CAST(? AS UUID)
            """, (
                bool(col.get("is_pii", False)),
                col.get("generator_type", "auto") or "auto",
                col.get("data_type") or "varchar",
                float(col.get("randomization_pct", 0.0) or 0.0),
                new_allowed_values,
                new_allowed_values_expanded,
                expand_categories,
                col_id,
            ))
            
            # Check if user provided stat overrides
            if any(k in col for k in ("null_value_percent", "min_val", "max_val", "sd", "variance")):
                null_pct = col.get("null_value_percent")
                min_v = col.get("min_val")
                max_v = col.get("max_val")
                sd_v = col.get("sd")
                var_v = col.get("variance")
                
                profile_exists = conn.execute("SELECT 1 FROM column_profiles WHERE column_id = CAST(? AS UUID)", (col_id,)).fetchone()
                if not profile_exists:
                    new_uuid = str(uuid.uuid4())
                    conn.execute("INSERT INTO column_profiles (id, column_id) VALUES (CAST(? AS UUID), CAST(? AS UUID))", (new_uuid, col_id))
                
                updates = []
                params = []
                
                if "null_value_percent" in col:
                    updates.append("null_value_percent = ?")
                    params.append(None if null_pct is None or str(null_pct).strip() == "" else float(null_pct))
                if "min_val" in col:
                    updates.append("min_val = ?")
                    params.append(None if min_v is None or str(min_v).strip() == "" else str(min_v))
                if "max_val" in col:
                    updates.append("max_val = ?")
                    params.append(None if max_v is None or str(max_v).strip() == "" else str(max_v))
                if "sd" in col:
                    updates.append("sd = ?")
                    params.append(None if sd_v is None or str(sd_v).strip() == "" else float(sd_v))
                if "variance" in col:
                    updates.append("variance = ?")
                    params.append(None if var_v is None or str(var_v).strip() == "" else float(var_v))
                    
                if updates:
                    params.append(col_id)
                    update_sql = f"UPDATE column_profiles SET {', '.join(updates)} WHERE column_id = CAST(? AS UUID)"
                    conn.execute(update_sql, params)
            updated_count += 1
        return {"message": "Config updated", "updated_count": updated_count}
    finally:
        conn.close()

@app.post("/project/{project_id}/infer-semantic-types")
async def infer_project_semantic_types(project_id: str, apply: bool = True):
    """
    Uses Groq LLM (with heuristic fallback) to infer semantic types from column names/types.
    If apply=true, inferred `generator_type` and `is_pii` values are saved into metadata DB.
    """
    conn = get_db_connection()
    try:
        raw_columns = safe_df_to_dict(conn.execute("""
            SELECT
                c.id AS column_id,
                t.name AS table_name,
                c.name AS column_name,
                c.data_type,
                c.generator_type,
                c.allowed_values,
                c.allowed_values_expanded,
                c.expand_categories,
                c.is_pii,
                p.cardinality,
                p.null_count,
                p.sd,
                p.variance,
                p.null_value_percent
            FROM columns c
            JOIN tables t ON c.table_id = t.id
            LEFT JOIN column_profiles p ON p.column_id = c.id
            WHERE t.project_id = ?
        """, (project_id,)).df())

        columns = []
        for c in raw_columns:
            columns.append({
                "column_id": str(c.get("column_id")) if c.get("column_id") is not None else "",
                "table_name": str(c.get("table_name")) if c.get("table_name") is not None else "",
                "column_name": str(c.get("column_name")) if c.get("column_name") is not None else "",
                "data_type": str(c.get("data_type")) if c.get("data_type") is not None else "",
                "generator_type": str(c.get("generator_type")) if c.get("generator_type") is not None else "auto",
                "allowed_values": _normalize_allowed_values(c.get("allowed_values")),
                "allowed_values_expanded": _normalize_allowed_values(c.get("allowed_values_expanded")),
                "expand_categories": bool(c.get("expand_categories", False)),
                "is_pii": bool(c.get("is_pii", False)),
                "cardinality": c.get("cardinality"),
                "null_count": c.get("null_count"),
                "sd": c.get("sd"),
                "variance": c.get("variance"),
                "null_value_percent": c.get("null_value_percent"),
            })
        columns_by_id = {str(c.get("column_id") or ""): c for c in columns}

        if not columns:
            raise HTTPException(status_code=404, detail="Project not found or has no columns")

        inference = await infer_column_semantics(columns)
        suggestions = inference.get("suggestions", [])

        applied_count = 0
        if apply:
            for suggestion in suggestions:
                col_id = suggestion.get("column_id")
                if not col_id:
                    continue
                conn.execute("""
                    UPDATE columns
                    SET is_pii = ?, generator_type = ?, allowed_values = ?, allowed_values_expanded = ?
                    WHERE id = CAST(? AS UUID)
                """, (
                    bool(suggestion.get("is_pii", False)),
                    suggestion.get("generator_type", "auto"),
                    _normalize_allowed_values(suggestion.get("allowed_values")) or _normalize_allowed_values(columns_by_id.get(col_id, {}).get("allowed_values")),
                    _normalize_allowed_values(columns_by_id.get(col_id, {}).get("allowed_values_expanded")),
                    col_id,
                ))
                applied_count += 1

        response_payload = {
            "project_id": project_id,
            "source": inference.get("source"),
            "model": inference.get("model"),
            "error": inference.get("error"),
            "applied": apply,
            "applied_count": applied_count,
            "suggestions": suggestions,
        }
        return jsonable_encoder(response_payload)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Semantic inference failed: {str(e)}")
    finally:
        conn.close()


@app.post("/project/{project_id}/expand-categories")
async def expand_project_categories(project_id: str, apply: bool = True, max_values: int = 12):
    max_values = max(2, min(int(max_values), 25))
    conn = get_db_connection()
    try:
        raw_columns = safe_df_to_dict(conn.execute("""
            SELECT
                c.id AS column_id,
                t.name AS table_name,
                c.name AS column_name,
                c.data_type,
                c.generator_type,
                c.allowed_values,
                c.allowed_values_expanded,
                c.expand_categories
            FROM columns c
            JOIN tables t ON c.table_id = t.id
            WHERE t.project_id = ?
        """, (project_id,)).df())
        if not raw_columns:
            raise HTTPException(status_code=404, detail="Project not found or has no columns")

        expansions = []
        applied_count = 0
        for col in raw_columns:
            data_type = str(col.get("data_type") or "")
            generator_type = str(col.get("generator_type") or "auto").strip().lower()
            effective_generator = generator_type if generator_type != "auto" else _infer_generator_from_dtype(data_type)
            seed_values = parse_allowed_values(col.get("allowed_values"))
            if not bool(col.get("expand_categories", False)) or effective_generator != "categorical" or len(seed_values) < 2:
                continue

            expansion = await expand_categorical_column(
                {
                    "column_id": str(col.get("column_id") or ""),
                    "table_name": str(col.get("table_name") or ""),
                    "column_name": str(col.get("column_name") or ""),
                    "data_type": data_type,
                    "generator_type": effective_generator,
                },
                seed_values=seed_values,
                max_values=max_values,
            )
            expanded_str = _normalize_allowed_values(expansion.get("expanded_values"))
            expansions.append(
                {
                    "column_id": str(col.get("column_id") or ""),
                    "table_name": str(col.get("table_name") or ""),
                    "column_name": str(col.get("column_name") or ""),
                    "seed_values": _normalize_allowed_values(col.get("allowed_values")),
                    "expanded_values": expanded_str,
                    "source": expansion.get("source"),
                    "model": expansion.get("model"),
                    "reason": expansion.get("reason"),
                    "error": expansion.get("error"),
                }
            )

            if apply:
                conn.execute(
                    """
                    UPDATE columns
                    SET allowed_values_expanded = ?
                    WHERE id = CAST(? AS UUID)
                    """,
                    (expanded_str, str(col.get("column_id") or "")),
                )
                applied_count += 1

        return jsonable_encoder(
            {
                "project_id": project_id,
                "applied": apply,
                "applied_count": applied_count,
                "expansions": expansions,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Category expansion failed: {str(e)}")
    finally:
        conn.close()


@app.post("/project/{project_id}/detect-pii")
async def detect_project_pii(project_id: str, apply: bool = True, sample_size: int = 50):
    """
    Detects PII columns using column-name signals, value regex signals, and optional spaCy NER.
    If apply=true, updates `columns.is_pii` for the project.
    """
    sample_size = max(5, min(int(sample_size), 200))
    conn = get_db_connection()
    try:
        columns = safe_df_to_dict(
            conn.execute(
                """
                SELECT
                    c.id AS column_id,
                    t.name AS table_name,
                    c.name AS column_name,
                    c.data_type,
                    t.file_path
                FROM columns c
                JOIN tables t ON c.table_id = t.id
                WHERE t.project_id = ?
                """,
                (project_id,),
            ).df()
        )
        if not columns:
            raise HTTPException(status_code=404, detail="Project not found or has no columns")

        normalized_cols = []
        for col in columns:
            normalized_cols.append(
                {
                    "column_id": str(col.get("column_id") or ""),
                    "table_name": str(col.get("table_name") or ""),
                    "column_name": str(col.get("column_name") or ""),
                    "data_type": str(col.get("data_type") or ""),
                    "file_path": str(col.get("file_path") or ""),
                }
            )

        value_samples: Dict[str, List[str]] = {}
        for col in normalized_cols:
            file_path = col.get("file_path", "")
            if not file_path:
                continue
            col_name = col["column_name"]
            col_id = col["column_id"]
            try:
                q_col = _quote_ident(col_name)
                query = (
                    f"SELECT {q_col} AS value "
                    "FROM read_csv_auto(?) "
                    f"WHERE {q_col} IS NOT NULL "
                    "LIMIT ?"
                )
                rows = conn.execute(query, (file_path, sample_size)).fetchall()
                value_samples[col_id] = [str(r[0]) for r in rows if r and r[0] is not None]
            except Exception:
                value_samples[col_id] = []

        detections = detect_pii_columns(normalized_cols, value_samples=value_samples)

        applied_count = 0
        if apply:
            for d in detections:
                conn.execute(
                    """
                    UPDATE columns
                    SET is_pii = ?
                    WHERE id = CAST(? AS UUID)
                    """,
                    (bool(d.get("is_pii", False)), d["column_id"]),
                )
                applied_count += 1

        pii_count = sum(1 for d in detections if d.get("is_pii"))
        return jsonable_encoder(
            {
                "project_id": project_id,
                "applied": apply,
                "applied_count": applied_count,
                "pii_detected_count": pii_count,
                "sample_size": sample_size,
                "detections": detections,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PII detection failed: {str(e)}")
    finally:
        conn.close()

@app.post("/project/{project_id}/infer-relations")
async def infer_project_relations(project_id: str, apply: bool = True):
    conn = get_db_connection()
    try:
        project_rows = safe_df_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).df())
        if not project_rows:
            raise HTTPException(status_code=404, detail="Project not found")

        raw_tables = safe_df_to_dict(conn.execute("SELECT * FROM tables WHERE project_id = ?", (project_id,)).df())
        if len(raw_tables) <= 1:
            return {
                "project_id": project_id,
                "source": "heuristic",
                "model": None,
                "applied": apply,
                "applied_count": 0,
                "relationships": [],
            }

        table_payload = []
        for t in raw_tables:
            table_id = t["id"]
            cols = safe_df_to_dict(
                conn.execute(
                    """
                    SELECT name, data_type, is_pk, is_nullable
                    FROM columns
                    WHERE table_id = ?
                    """,
                    (table_id,),
                ).df()
            )
            table_payload.append({"name": t["name"], "columns": cols})

        inference = await infer_table_relationships(table_payload)
        relationships = inference.get("relationships", [])

        applied_count = 0
        if apply:
            conn.execute("DELETE FROM relations WHERE project_id = CAST(? AS UUID)", (project_id,))
            for rel in relationships:
                conn.execute(
                    """
                    INSERT INTO relations (id, project_id, from_table, from_column, to_table, to_column, cardinality, is_optional)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        project_id,
                        rel["from_table"],
                        rel["from_column"],
                        rel["to_table"],
                        rel["to_column"],
                        rel.get("cardinality", "1:N"),
                        bool(rel.get("is_optional", True)),
                    ),
                )
                applied_count += 1

        return {
            "project_id": project_id,
            "source": inference.get("source"),
            "model": inference.get("model"),
            "error": inference.get("error"),
            "applied": apply,
            "applied_count": applied_count,
            "relationships": relationships,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Relationship inference failed: {str(e)}")
    finally:
        conn.close()

@app.post("/project/{project_id}/relations/update")
async def update_project_relations(project_id: str, payload = Body(...)):
    if isinstance(payload, dict):
        relations = payload.get("relations", [])
    elif isinstance(payload, list):
        relations = payload
    else:
        raise HTTPException(status_code=422, detail="Invalid payload. Expected list or {relations:[...]}.")

    conn = get_db_connection()
    try:
        project_rows = safe_df_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).df())
        if not project_rows:
            raise HTTPException(status_code=404, detail="Project not found")

        raw_tables = safe_df_to_dict(conn.execute("SELECT * FROM tables WHERE project_id = ?", (project_id,)).df())
        table_names = {str(t["name"]) for t in raw_tables}
        cols_by_table = {}
        for t in raw_tables:
            tname = str(t["name"])
            cols = safe_df_to_dict(
                conn.execute("SELECT name FROM columns WHERE table_id = ?", (t["id"],)).df()
            )
            cols_by_table[tname] = {str(c["name"]) for c in cols}

        conn.execute("DELETE FROM relations WHERE project_id = CAST(? AS UUID)", (project_id,))
        applied_count = 0
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            ft = str(rel.get("from_table") or "").strip()
            fc = str(rel.get("from_column") or "").strip()
            tt = str(rel.get("to_table") or "").strip()
            tc = str(rel.get("to_column") or "").strip()
            if not ft or not fc or not tt or not tc:
                continue
            if ft not in table_names or tt not in table_names:
                continue
            if fc not in cols_by_table.get(ft, set()) or tc not in cols_by_table.get(tt, set()):
                continue

            cardinality = str(rel.get("cardinality") or "1:N").upper()
            if cardinality not in {"1:N", "1:1", "N:1", "N:N"}:
                cardinality = "1:N"
            is_optional = bool(rel.get("is_optional", True))

            conn.execute(
                """
                INSERT INTO relations (id, project_id, from_table, from_column, to_table, to_column, cardinality, is_optional)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), project_id, ft, fc, tt, tc, cardinality, is_optional),
            )
            applied_count += 1

        return {"message": "Relations updated", "updated_count": applied_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Relations update failed: {str(e)}")
    finally:
        conn.close()

@app.post("/project/{project_id}/add-table")
async def add_table(project_id: str, file: UploadFile = File(...)):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed")
    
    table_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, f"{table_id}.csv")
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        stats = get_csv_stats(file_path)
        conn = get_db_connection()
        # Create Table
        conn.execute("INSERT INTO tables (id, project_id, name, file_path, row_count) VALUES (?, ?, ?, ?, ?)",
                     (table_id, project_id, file.filename.replace('.csv', ''), file_path, stats["total_rows"]))
        
        # Create Columns and Profiles
        for col in stats["columns"]:
            col_id = str(uuid.uuid4())
            conn.execute("INSERT INTO columns (id, table_id, name, data_type) VALUES (?, ?, ?, ?)", 
                         (col_id, table_id, col["column"], col["type"]))
            conn.execute("INSERT INTO column_profiles (id, column_id, null_count, min_val, max_val, cardinality, sd, variance, null_value_percent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (str(uuid.uuid4()), col_id, col["nulls"], col["min"], col["max"], col["cardinality"], col.get("sd"), col.get("variance"), col.get("null_value_percent")))
        conn.close()
        return {"table_id": table_id, "stats": stats}
    except Exception as e:
        if os.path.exists(file_path): os.remove(file_path)
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/project/{project_id}/tables/{table_id}")
async def delete_table(project_id: str, table_id: str):
    conn = get_db_connection()
    try:
        table_rows = conn.execute(
            """
            SELECT id, name, file_path
            FROM tables
            WHERE project_id = CAST(? AS UUID) AND id = CAST(? AS UUID)
            """,
            (project_id, table_id),
        ).df().to_dict("records")

        if not table_rows:
            raise HTTPException(status_code=404, detail="Table not found in project")

        table = table_rows[0]
        table_name = table.get("name")
        file_path = table.get("file_path")

        conn.execute(
            """
            DELETE FROM column_profiles
            WHERE column_id IN (
                SELECT id FROM columns WHERE table_id = CAST(? AS UUID)
            )
            """,
            (table_id,),
        )
        conn.execute("DELETE FROM columns WHERE table_id = CAST(? AS UUID)", (table_id,))
        conn.execute(
            """
            DELETE FROM relations
            WHERE project_id = CAST(? AS UUID) AND (from_table = ? OR to_table = ?)
            """,
            (project_id, table_name, table_name),
        )
        conn.execute("DELETE FROM tables WHERE id = CAST(? AS UUID)", (table_id,))

        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

        remaining_tables = conn.execute(
            "SELECT COUNT(*) AS cnt FROM tables WHERE project_id = CAST(? AS UUID)",
            (project_id,),
        ).fetchone()[0]

        project_deleted = False
        if int(remaining_tables) == 0:
            conn.execute("DELETE FROM relations WHERE project_id = CAST(? AS UUID)", (project_id,))
            conn.execute("DELETE FROM projects WHERE id = CAST(? AS UUID)", (project_id,))
            project_deleted = True

        return {
            "project_id": project_id,
            "table_id": table_id,
            "project_deleted": project_deleted,
            "remaining_tables": int(remaining_tables),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
    finally:
        conn.close()

@app.delete("/project/{project_id}")
async def delete_project(project_id: str):
    conn = get_db_connection()
    try:
        project_rows = conn.execute(
            "SELECT id FROM projects WHERE id = CAST(? AS UUID)",
            (project_id,),
        ).df().to_dict("records")
        if not project_rows:
            raise HTTPException(status_code=404, detail="Project not found")

        table_rows = conn.execute(
            "SELECT id, file_path FROM tables WHERE project_id = CAST(? AS UUID)",
            (project_id,),
        ).df().to_dict("records")

        conn.execute(
            """
            DELETE FROM column_profiles
            WHERE column_id IN (
                SELECT c.id
                FROM columns c
                JOIN tables t ON c.table_id = t.id
                WHERE t.project_id = CAST(? AS UUID)
            )
            """,
            (project_id,),
        )
        conn.execute(
            """
            DELETE FROM columns
            WHERE table_id IN (
                SELECT id FROM tables WHERE project_id = CAST(? AS UUID)
            )
            """,
            (project_id,),
        )
        conn.execute("DELETE FROM relations WHERE project_id = CAST(? AS UUID)", (project_id,))
        conn.execute("DELETE FROM tables WHERE project_id = CAST(? AS UUID)", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = CAST(? AS UUID)", (project_id,))

        deleted_files = 0
        for row in table_rows:
            file_path = row.get("file_path")
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    deleted_files += 1
                except Exception:
                    pass

        return {
            "project_id": project_id,
            "deleted_tables": len(table_rows),
            "deleted_files": deleted_files,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Project delete failed: {str(e)}")
    finally:
        conn.close()

@app.post("/project/{project_id}/add-relation")
async def add_relation(project_id: str, from_table: str, from_col: str, to_table: str, to_col: str):
    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT INTO relations (id, project_id, from_table, from_column, to_table, to_column) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), project_id, from_table, from_col, to_table, to_col))
        return {"message": "Relation added"}
    finally:
        conn.close()

def run_generation_task(
    task_id: str,
    project_id: str,
    num_rows: int,
    seed: int,
    output_format: str,
    table_settings: Optional[Dict[str, Dict[str, int]]] = None,
    stddev_scale: float = 1.0,
    variation_pct: float = 0.0,
    knn_smoothing: float = 0.0,
    knn_neighbors: int = 5,
):
    start_time = time.time()
    tasks[task_id] = {"status": "running", "progress": 0, "logs": [f"Starting generation for project {project_id}..."], "file_path": None}
    try:
        generation_params = {
            "stddev_scale": float(stddev_scale),
            "variation_pct": float(variation_pct),
            "knn_smoothing": float(knn_smoothing),
            "knn_neighbors": int(knn_neighbors),
        }
        tasks[task_id]["progress"] = 1
        tasks[task_id]["logs"].append(f"Initialization: Fetching metadata and planning...")
        conn = get_db_connection()
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).df().to_dict('records')[0]
        tables = conn.execute("SELECT * FROM tables WHERE project_id = ?", (project_id,)).df().to_dict('records')
        relations = conn.execute("SELECT * FROM relations WHERE project_id = ?", (project_id,)).df().to_dict('records')
        column_configs = conn.execute(
            """
            SELECT
                t.name AS table_name,
                c.name AS column_name,
                c.data_type,
                c.is_pk,
                c.generator_type,
                c.allowed_values,
                c.allowed_values_expanded,
                c.is_pii,
                c.randomization_pct,
                c.expand_categories,
                p.null_value_percent,
                p.min_val,
                p.max_val,
                p.sd,
                p.variance
            FROM columns c
            JOIN tables t ON c.table_id = t.id
            LEFT JOIN column_profiles p ON p.column_id = c.id
            WHERE t.project_id = ?
            """,
            (project_id,),
        ).df().to_dict("records")
        conn.close()
        key_columns = _build_key_column_set(relations)
        table_names = [str(t.get("name") or "") for t in tables if str(t.get("name") or "").strip()]
        table_settings = _normalize_table_generation_settings(table_settings, table_names, num_rows, seed)
        table_row_counts = {name: cfg["num_rows"] for name, cfg in table_settings.items()}
        table_seed_map = {name: cfg["seed"] for name, cfg in table_settings.items()}
        cfg_by_table: Dict[str, List[Dict[str, Any]]] = {}
        for cfg in column_configs:
            tname = str(cfg.get("table_name") or "")
            cfg_by_table.setdefault(tname, []).append(cfg)

        expanded_value_logs = []
        for cfg in column_configs:
            gen_type = str(cfg.get("generator_type") or "").strip().lower()
            resolved_values = _resolve_allowed_values(cfg)
            if gen_type == "categorical" and resolved_values:
                expanded_value_logs.append(
                    f"{cfg.get('table_name')}.{cfg.get('column_name')}: {resolved_values}"
                )

        tasks[task_id]["logs"].append(f"Source type: {project['source_type']}, Tables: {len(tables)}, Relations: {len(relations)}")
        if table_settings:
            tasks[task_id]["logs"].append("Per-table generation settings:")
            for table_name in table_names:
                cfg = table_settings.get(table_name) or {"num_rows": num_rows, "seed": seed}
                tasks[task_id]["logs"].append(f"  {table_name}: rows={int(cfg['num_rows'])}, seed={int(cfg['seed'])}")
        if expanded_value_logs:
            tasks[task_id]["logs"].append("Categorical values in use:")
            for line in expanded_value_logs:
                tasks[task_id]["logs"].append(f"  {line}")
        if variation_pct > 0 or not np.isclose(stddev_scale, 1.0) or knn_smoothing > 0:
            tasks[task_id]["logs"].append(
                f"Generation parameters: stddev_scale={float(stddev_scale):.2f}, variation_pct={float(variation_pct):.1f}, "
                f"knn_smoothing={float(knn_smoothing):.2f}, knn_neighbors={int(knn_neighbors)}"
            )
        tasks[task_id]["progress"] = 5

        if project['source_type'] == 'CSV':
            if len(tables) == 1:
                table = tables[0]
                table_name = str(table["name"])
                table_rows = int(table_row_counts.get(table_name, num_rows))
                table_seed = int(table_seed_map.get(table_name, seed))
                tasks[task_id]["logs"].append(f"Single-table CSV synthesis target: {table_rows} rows.")
                tasks[task_id]["progress"] = 10
                ext = ".csv" if output_format == "csv" else ".parquet"
                output_filename = f"synth_{task_id}{ext}"
                output_path = os.path.join(EXPORT_DIR, output_filename)
                
                tasks[task_id]["logs"].append("Fitting model and sampling data (GaussianCopula)...")
                tasks[task_id]["progress"] = 15
                temp_csv = os.path.join(EXPORT_DIR, f"temp_{task_id}.csv")
                generate_synthetic_data(table['file_path'], temp_csv, table_rows, seed=table_seed)
                tasks[task_id]["progress"] = 70

                synth_df = pd.read_csv(temp_csv)
                source_df = pd.read_csv(table["file_path"])
                synth_df = _apply_generation_parameters(
                    table_name=table["name"],
                    df=synth_df,
                    column_configs=cfg_by_table.get(str(table["name"]), []),
                    seed=table_seed,
                    generation_params=generation_params,
                    source_df=source_df,
                )
                synth_df = _apply_modeling_config_to_df(
                    table_name=table["name"],
                    df=synth_df,
                    column_configs=cfg_by_table.get(str(table["name"]), []),
                    key_columns=key_columns,
                    seed=table_seed,
                )
                tasks[task_id]["progress"] = 85
                if output_format == "parquet":
                    tasks[task_id]["logs"].append("Applying modeling config and exporting Parquet...")
                    synth_df.to_parquet(output_path, index=False)
                else:
                    tasks[task_id]["logs"].append("Applying modeling config and exporting CSV...")
                    synth_df.to_csv(output_path, index=False)
                os.remove(temp_csv)
                
                tasks[task_id]["file_path"] = output_path
                tasks[task_id]["progress"] = 100
                tasks[task_id]["status"] = "done"
                
                duration = round(time.time() - start_time, 2)
                tasks[task_id]["logs"].append(f"Generation successful in {duration}s. Output: {output_filename}")
            else:
                tasks[task_id]["progress"] = 10
                tasks[task_id]["logs"].append(f"Multi-table HMA synthesis target scale: {num_rows} base rows.")
                tasks[task_id]["logs"].append(f"Loading {len(tables)} source tables into memory...")
                table_dict = {}
                for i, t in enumerate(tables):
                    tasks[task_id]["logs"].append(f"  Loading table: {t['name']}...")
                    table_dict[t['name']] = pd.read_csv(t['file_path'])
                    tasks[task_id]["progress"] = 10 + int(10 * (i + 1) / len(tables))
                
                tasks[task_id]["progress"] = 20
                def _progress_cb(pct):
                    # Map 0-100 from synthesizer into 20-75 range for the overall pipeline
                    tasks[task_id]["progress"] = 20 + int(pct * 0.55)
                tasks[task_id]["logs"].append("Fast per-table synthesis with FK stitching...")
                try:
                    synth_dict = generate_multi_table_data_fast(
                        table_dict,
                        relations,
                        num_rows,
                        seed,
                        row_counts=table_row_counts,
                        table_seeds=table_seed_map,
                        progress_callback=_progress_cb,
                    )
                except Exception as fast_err:
                    tasks[task_id]["logs"].append(f"Fast path failed ({fast_err}), falling back to HMA...")
                    synth_dict = generate_multi_table_data(table_dict, relations, num_rows, seed)
                    for t_name, target_rows in table_row_counts.items():
                        if t_name in synth_dict:
                            synth_dict[t_name] = _resize_dataframe_to_count(
                                synth_dict[t_name],
                                target_rows,
                                table_seed_map.get(t_name, seed),
                            )
                tasks[task_id]["progress"] = 75
                tasks[task_id]["logs"].append("Applying modeling config to generated tables...")
                for t_name, df in list(synth_dict.items()):
                    table_seed = int(table_seed_map.get(str(t_name), seed))
                    df = _apply_generation_parameters(
                        table_name=t_name,
                        df=df,
                        column_configs=cfg_by_table.get(str(t_name), []),
                        seed=table_seed,
                        generation_params=generation_params,
                        source_df=table_dict.get(t_name),
                    )
                    synth_dict[t_name] = _apply_modeling_config_to_df(
                        table_name=t_name,
                        df=df,
                        column_configs=cfg_by_table.get(str(t_name), []),
                        key_columns=key_columns,
                        seed=table_seed,
                    )
                tasks[task_id]["progress"] = 80

                tasks[task_id]["logs"].append("Packaging results into archive...")
                output_filename = f"multi_synth_{task_id}.zip"
                output_path = os.path.join(EXPORT_DIR, output_filename)
                with zipfile.ZipFile(output_path, "w") as z:
                    for t_name, df in synth_dict.items():
                        if output_format == "parquet":
                            buf = io.BytesIO()
                            df.to_parquet(buf)
                            z.writestr(f"{t_name}.parquet", buf.getvalue())
                        else:
                            z.writestr(f"{t_name}.csv", df.to_csv(index=False))
                
                tasks[task_id]["file_path"] = output_path
                tasks[task_id]["progress"] = 100
                tasks[task_id]["status"] = "done"
                duration = round(time.time() - start_time, 2)
                tasks[task_id]["logs"].append(f"Multi-table generation successful in {duration}s.")

        else:
            tasks[task_id]["logs"].append(f"Schema-based mock generation starting for {num_rows} rows...")
            conn = get_db_connection()
            tables_data = conn.execute("""
                SELECT t.name as table_name, c.name as column_name, c.data_type, c.is_pk as is_primary_key, c.generator_type, c.is_nullable, c.allowed_values, c.allowed_values_expanded
                FROM tables t JOIN columns c ON t.id = c.table_id WHERE t.project_id = ?
            """, (project_id,)).df().to_dict('records')
            
            tables_metadata = conn.execute("SELECT * FROM tables WHERE project_id = ?", (project_id,)).df().to_dict('records')
            planner = RelationalPlanner(tables_metadata, relations)
            plan = planner.get_plan(num_rows)
            plan_row_counts = dict(plan.get("row_counts") or {})
            for table_name, rows in table_row_counts.items():
                plan_row_counts[table_name] = int(rows)
            conn.close()

            tasks[task_id]["logs"].append("Generating relational data directly into DuckDB Sandbox...")
            
            # Create a localized sandbox for this task
            sandbox_path = os.path.join(EXPORT_DIR, f"sandbox_{task_id}.db")
            sandbox_conn = duckdb.connect(sandbox_path)
            
            table_names = generate_mock_from_schema(
                tables_data, 
                relations, 
                num_rows, 
                sandbox_conn,
                seed,
                order=plan['order'],
                row_counts=plan_row_counts,
                table_seeds=table_seed_map,
            )
            tasks[task_id]["progress"] = 90

            tasks[task_id]["logs"].append(f"Exporting {len(table_names)} tables from Sandbox to Archive...")
            output_filename = f"mock_{task_id}.zip"
            output_path = os.path.join(EXPORT_DIR, output_filename)
            
            with zipfile.ZipFile(output_path, "w") as z:
                for t_name in table_names:
                    # Pull table out of DuckDB sandbox, apply modeling configs, then export.
                    ext = "csv" if output_format == "csv" else "parquet"
                    tmp_file = os.path.join(EXPORT_DIR, f"temp_{t_name}_{task_id}.{ext}")
                    q_t_name = _quote_ident(t_name)
                    table_seed = int(table_seed_map.get(str(t_name), seed))

                    df = sandbox_conn.execute(f"SELECT * FROM {q_t_name}").df()
                    df = _apply_generation_parameters(
                        table_name=t_name,
                        df=df,
                        column_configs=cfg_by_table.get(str(t_name), []),
                        seed=table_seed,
                        generation_params=generation_params,
                        source_df=None,
                    )
                    df = _apply_modeling_config_to_df(
                        table_name=t_name,
                        df=df,
                        column_configs=cfg_by_table.get(str(t_name), []),
                        key_columns=key_columns,
                        seed=table_seed,
                    )
                    if output_format == "csv":
                        df.to_csv(tmp_file, index=False)
                    else:
                        df.to_parquet(tmp_file, index=False)

                    z.write(tmp_file, f"{t_name}.{ext}")
                    os.remove(tmp_file)
            
            sandbox_conn.close()
            if os.path.exists(sandbox_path): os.remove(sandbox_path)
            
            tasks[task_id]["file_path"] = output_path
            tasks[task_id]["progress"] = 100
            tasks[task_id]["status"] = "done"
            duration = round(time.time() - start_time, 2)
            tasks[task_id]["logs"].append(f"Direct-to-Disk generation successful in {duration}s.")

    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["logs"].append(f"CRITICAL ERROR: {str(e)}")
        # Log the full error to stdout for debugging
        import traceback
        traceback.print_exc()

@app.get("/generate/{project_id}")
async def generate_dispatch(
    project_id: str,
    background_tasks: BackgroundTasks,
    num_rows: float = 100,
    seed: int = 42,
    format: str = "csv",
    table_settings_json: str = "",
    stddev_scale: float = 1.0,
    variation_pct: float = 0.0,
    knn_smoothing: float = 0.0,
    knn_neighbors: int = 5,
):
    task_id = str(uuid.uuid4())
    # Cast num_rows to int in case it came as a float string from JS
    rows_int = int(num_rows)
    try:
        table_settings = json.loads(table_settings_json) if str(table_settings_json or "").strip() else {}
    except Exception:
        table_settings = {}
    background_tasks.add_task(
        run_generation_task,
        task_id,
        project_id,
        rows_int,
        seed,
        format,
        table_settings,
        float(stddev_scale),
        float(variation_pct),
        float(knn_smoothing),
        int(knn_neighbors),
    )
    return {"task_id": task_id}

@app.get("/task/{task_id}")
async def get_task_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]

@app.get("/task/{task_id}/download")
async def download_task_file(task_id: str):
    if task_id not in tasks or tasks[task_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="File not ready or task failed")
    file_path = tasks[task_id]["file_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    filename = os.path.basename(file_path)
    
    # Determine media type based on file extension
    if filename.endswith('.csv'):
        media_type = "text/csv"
    elif filename.endswith('.zip'):
        media_type = "application/zip"
    elif filename.endswith('.parquet'):
        media_type = "application/octet-stream"
    else:
        media_type = "application/octet-stream"
    
    # Use StreamingResponse for ZIP files to avoid timeout issues
    if filename.endswith('.zip'):
        def iterfile():
            with open(file_path, "rb") as f:
                yield from f
        
        return StreamingResponse(
            iterfile(),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    # FileResponse for CSV and other files
    return FileResponse(
        file_path, 
        filename=filename,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/task/{task_id}/preview")
async def preview_task_file(task_id: str, rows: int = 5):
    if task_id not in tasks or tasks[task_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="Preview not ready or task failed")
    file_path = tasks[task_id].get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Generated file not found")

    preview_rows = max(1, min(int(rows), 25))
    tables = _load_synth_tables_from_output(file_path)
    previews = []
    for table_name, df in tables.items():
        preview_df = df.head(preview_rows).replace({np.nan: None, np.inf: None, -np.inf: None})
        previews.append(
            {
                "table_name": table_name,
                "columns": [str(c) for c in preview_df.columns.tolist()],
                "rows": jsonable_encoder(preview_df.to_dict("records")),
            }
        )
    return {"task_id": task_id, "rows": preview_rows, "tables": previews}

@app.get("/project/{project_id}/plan")
async def get_project_plan(project_id: str, base_rows: int = 100):
    conn = get_db_connection()
    try:
        tables = safe_df_to_dict(conn.execute("SELECT * FROM tables WHERE project_id = ?", (project_id,)).df())
        relations = safe_df_to_dict(conn.execute("SELECT * FROM relations WHERE project_id = ?", (project_id,)).df())
        
        if not tables:
            raise HTTPException(status_code=404, detail="Project not found")
            
        planner = RelationalPlanner(tables, relations)
        return planner.get_plan(base_rows)
    finally:
        conn.close()

async def generate_from_schema(project_id: str, num_rows: int = 100, seed: int = 42):
    conn = get_db_connection()
    try:
        # Unified table/column fetch
        tables_data = conn.execute("""
            SELECT t.name as table_name, c.name as column_name, c.data_type, c.is_pk as is_primary_key, c.generator_type, c.is_nullable 
            FROM tables t 
            JOIN columns c ON t.id = c.table_id 
            WHERE t.project_id = ?
        """, (project_id,)).df().to_dict('records')
        
        relations = conn.execute("SELECT * FROM relations WHERE project_id = ?", (project_id,)).df().to_dict('records')
        
        if not tables_data:
            raise HTTPException(status_code=404, detail="Project not found or has no tables")
            
        # Get execution plan
        conn_tables = conn.execute("SELECT * FROM tables WHERE project_id = ?", (project_id,)).df().to_dict('records')
        planner = RelationalPlanner(conn_tables, relations)
        plan = planner.get_plan(num_rows)
        
        df_dict = generate_mock_from_schema(
            tables_data, 
            relations, 
            num_rows, 
            seed,
            order=plan['order'],
            row_counts=plan['row_counts']
        )
        
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            for table_name, df in df_dict.items():
                zip_file.writestr(f"{table_name}.csv", df.to_csv(index=False))
        
        zip_buffer.seek(0)
        return StreamingResponse(
            zip_buffer,
            media_type="application/x-zip-compressed",
            headers={"Content-Disposition": f"attachment; filename=project_{project_id}_data.zip"}
        )
    finally:
        conn.close()

@app.get("/download/{project_id}")
async def download(project_id: str):
    output_filename = f"synthetic_{project_id}.csv"
    output_path = os.path.join(EXPORT_DIR, output_filename)
    if not os.path.exists(output_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=output_path, filename=f"synthetic_data_{project_id}.csv", media_type='text/csv')

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
