import pandas as pd
import numpy as np
from sdv.multi_table import HMASynthesizer
from sdv.single_table import GaussianCopulaSynthesizer
from sdv.metadata import MultiTableMetadata, SingleTableMetadata
from sdv.metadata.errors import InvalidMetadataError
from sdv.errors import InvalidDataError
from sdv.utils import drop_unknown_references
from typing import Dict, List, Any, Optional
import time


def _normalize_rel_value(value: Any) -> str:
    """Normalize relation payload values coming from UI/DB into a clean column/table name."""
    if value is None:
        return ""
    if isinstance(value, (set, list, tuple)):
        if not value:
            return ""
        value = sorted([str(v) for v in value])[0]
    return str(value).strip()


def _parse_relationships(
    relationships: List[Dict[str, Any]],
    table_data: Dict[str, pd.DataFrame],
) -> List[Dict[str, str]]:
    """Parse and validate relationships into a clean list of (parent_table, child_table, parent_pk, child_fk)."""
    cols_by_table = {str(t): set(df.columns.tolist()) for t, df in table_data.items()}
    parsed = []
    seen = set()

    for rel in relationships:
        parent_table = _normalize_rel_value(rel.get('to_table'))
        child_table = _normalize_rel_value(rel.get('from_table'))
        parent_pk = _normalize_rel_value(rel.get('to_column'))
        child_fk = _normalize_rel_value(rel.get('from_column'))

        if not all([parent_table, child_table, parent_pk, child_fk]):
            continue
        if parent_table not in table_data or child_table not in table_data:
            continue
        if child_fk not in cols_by_table.get(child_table, set()):
            continue
        if parent_pk not in cols_by_table.get(parent_table, set()):
            continue

        key = (parent_table, child_table, parent_pk, child_fk)
        if key in seen:
            continue
        seen.add(key)
        parsed.append({
            "parent_table": parent_table,
            "child_table": child_table,
            "parent_pk": parent_pk,
            "child_fk": child_fk,
        })
    return parsed


# ---------------------------------------------------------------------------
# FAST PATH: Per-table GaussianCopula + FK stitching
# ---------------------------------------------------------------------------

def generate_multi_table_data_fast(
    table_data: Dict[str, pd.DataFrame],
    relationships: List[Dict[str, Any]],
    num_rows: int = 100,
    seed: int = 42,
    row_counts: Optional[Dict[str, int]] = None,
    table_seeds: Optional[Dict[str, int]] = None,
    progress_callback: Optional[callable] = None,
) -> Dict[str, pd.DataFrame]:
    """
    Fast multi-table synthesis using per-table GaussianCopulaSynthesizer.
    ~10-50x faster than HMA because each table is modeled independently,
    then FK columns are stitched to maintain referential integrity.
    """
    import random
    random.seed(seed)
    np.random.seed(seed)

    parsed_rels = _parse_relationships(relationships, table_data)

    # Build dependency graph to determine generation order (parents first)
    all_tables = set(table_data.keys())
    children_of = {}  # parent -> [(child, pk, fk)]
    parent_of = {}    # child -> [(parent, pk, fk)]
    for rel in parsed_rels:
        children_of.setdefault(rel["parent_table"], []).append(
            (rel["child_table"], rel["parent_pk"], rel["child_fk"])
        )
        parent_of.setdefault(rel["child_table"], []).append(
            (rel["parent_table"], rel["parent_pk"], rel["child_fk"])
        )

    # Topological sort: tables with no parents go first
    ordered = []
    visited = set()
    def topo(table):
        if table in visited:
            return
        visited.add(table)
        for parent, _, _ in parent_of.get(table, []):
            topo(parent)
        ordered.append(table)
    for t in sorted(all_tables):
        topo(t)

    synth_results: Dict[str, pd.DataFrame] = {}
    total = len(ordered)

    for idx, table_name in enumerate(ordered):
        src_df = table_data[table_name]
        current_seed = int((table_seeds or {}).get(table_name, seed + idx))

        # Detect single-table metadata
        meta = SingleTableMetadata()
        meta.detect_from_dataframe(data=src_df)

        # Fit GaussianCopula (fast — statistical, not deep-learning)
        synthesizer = GaussianCopulaSynthesizer(meta, enforce_min_max_values=True)

        if current_seed is not None:
            try:
                import torch
                torch.manual_seed(current_seed)
            except ImportError:
                pass

        synthesizer.fit(src_df)

        if row_counts and table_name in row_counts:
            table_rows = max(1, int(row_counts[table_name]))
        else:
            # Calculate rows for this table proportional to source distribution
            scale = num_rows / max(len(src_df), 1)
            table_rows = max(1, int(len(src_df) * scale))
        synth_df = synthesizer.sample(num_rows=table_rows)

        # Stitch FK columns: replace FK values with valid parent PK values
        for parent_table, parent_pk, child_fk in parent_of.get(table_name, []):
            if parent_table in synth_results and child_fk in synth_df.columns:
                parent_pk_values = synth_results[parent_table][parent_pk].values
                if len(parent_pk_values) > 0:
                    rng = np.random.default_rng(current_seed + hash(child_fk))
                    synth_df[child_fk] = rng.choice(
                        parent_pk_values, size=len(synth_df), replace=True
                    )

        synth_results[table_name] = synth_df

        if progress_callback:
            progress_callback(int((idx + 1) / total * 100))

    return synth_results


# ---------------------------------------------------------------------------
# SLOW PATH: Full HMA (kept as fallback)
# ---------------------------------------------------------------------------

def generate_multi_table_data(
    table_data: Dict[str, pd.DataFrame],
    relationships: List[Dict[str, Any]],
    num_rows_scale: int = 100,
    seed: int = 42
):
    """
    Trains an HMASynthesizer on multiple related tables and samples a new dataset.
    
    table_data: { "table_name": df }
    relationships: [ { "from_table": "orders", "from_column": "user_id", "to_table": "users", "to_column": "id" } ]
    """
    metadata = MultiTableMetadata()
    
    # 1. Detect per-table metadata only; relationships are added explicitly below.
    for table_name, df in table_data.items():
        metadata.detect_table_from_dataframe(
            table_name=table_name,
            data=df,
            infer_sdtypes=True,
            infer_keys="primary_only",
        )

    meta_dict = metadata.to_dict()
    meta_tables = meta_dict.get("tables", {})
    pk_by_table = {str(t): str(cfg.get("primary_key") or "") for t, cfg in meta_tables.items()}
    cols_by_table = {str(t): set(df.columns.tolist()) for t, df in table_data.items()}

    # Remove any inferred relationships first; we re-add only validated user/model relationships.
    try:
        inferred_rels = list(metadata.relationships or [])
        for rel in inferred_rels:
            p = _normalize_rel_value(rel.get("parent_table_name"))
            c = _normalize_rel_value(rel.get("child_table_name"))
            if p and c:
                metadata.remove_relationship(parent_table_name=p, child_table_name=c)
    except Exception:
        pass

    # Relationships may already exist; keep a guard set to avoid duplicates.
    existing_relationships = set()
    try:
        for rel in metadata.to_dict().get("relationships", []):
            key = (
                str(rel.get("parent_table_name", "")),
                str(rel.get("child_table_name", "")),
                str(rel.get("parent_primary_key", "")),
                str(rel.get("child_foreign_key", "")),
            )
            if all(key):
                existing_relationships.add(key)
    except Exception:
        pass
    
    # 2. Add relationships (deduplicated to avoid SDV InvalidMetadataError)
    seen_relationships = set()
    for rel in relationships:
        parent_table = _normalize_rel_value(rel.get('to_table'))
        child_table = _normalize_rel_value(rel.get('from_table'))
        parent_pk = _normalize_rel_value(rel.get('to_column'))
        child_fk = _normalize_rel_value(rel.get('from_column'))

        if not parent_table or not child_table or not parent_pk or not child_fk:
            continue
        if parent_table not in table_data or child_table not in table_data:
            continue
        if child_fk not in cols_by_table.get(str(child_table), set()):
            continue

        detected_parent_pk = pk_by_table.get(str(parent_table), "")
        if not detected_parent_pk:
            continue
        if parent_pk not in cols_by_table.get(str(parent_table), set()):
            parent_pk = detected_parent_pk
        if parent_pk != detected_parent_pk:
            parent_pk = detected_parent_pk
        if parent_pk not in cols_by_table.get(str(parent_table), set()):
            continue
        parent_vals = set(table_data[parent_table][parent_pk].dropna().tolist())
        child_vals = set(table_data[child_table][child_fk].dropna().tolist())
        if parent_vals and child_vals and not (parent_vals & child_vals):
            continue

        rel_key = (str(parent_table), str(child_table), str(parent_pk), str(child_fk))
        if rel_key in seen_relationships:
            continue
        if rel_key in existing_relationships:
            continue
        seen_relationships.add(rel_key)

        try:
            metadata.add_relationship(
                parent_table_name=parent_table,
                child_table_name=child_table,
                parent_primary_key=parent_pk,
                child_foreign_key=child_fk
            )
            existing_relationships.add(rel_key)
        except InvalidMetadataError as ex:
            if "already been added" in str(ex):
                continue
            raise
    
    # 3. Initialize and fit
    synthesizer = HMASynthesizer(metadata)
    
    if seed is not None:
        import torch
        import random
        torch.manual_seed(seed)
        np.random.seed(seed)
        random.seed(seed)
        
    try:
        cleaned_data = drop_unknown_references(
            data=table_data,
            metadata=metadata,
            drop_missing_values=False,
            verbose=False,
        )
    except InvalidDataError:
        cleaned_data = table_data
    synthesizer.fit(cleaned_data)
    
    # 4. Sample
    max_source_rows = max(len(df) for df in table_data.values()) if table_data else 1
    if num_rows_scale > 0 and max_source_rows > 0:
        scale = num_rows_scale / max_source_rows
    else:
        scale = 1.0
    synthetic_data = synthesizer.sample(scale=scale)
    
    return synthetic_data
