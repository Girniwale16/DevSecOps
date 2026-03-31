import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from app.engine.llm_usage import extract_usage

load_dotenv()
repo_env = Path(__file__).resolve().parents[3] / ".env"
if repo_env.exists():
    load_dotenv(repo_env)

GROQ_API_URL = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = os.getenv("GROQ_REL_MODEL", os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"))


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None

    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _singularize(name: str) -> str:
    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"
    if name.endswith("ses") and len(name) > 3:
        return name[:-2]
    if name.endswith("s") and len(name) > 1:
        return name[:-1]
    return name


def _find_pk(columns: List[Dict[str, Any]], table_name: str) -> Optional[str]:
    for c in columns:
        if bool(c.get("is_pk", False)):
            return _to_text(c.get("name"))

    names = [_to_text(c.get("name")) for c in columns]
    if "id" in names:
        return "id"

    table_norm = _normalize_name(table_name)
    candidates = [f"{table_norm}id", f"{_singularize(table_norm)}id"]
    for c in names:
        if _normalize_name(c) in candidates:
            return c
    return names[0] if names else None


def _heuristic_infer(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    table_map = {_to_text(t.get("name")): t for t in tables}
    if len(table_map) <= 1:
        return []

    table_norm_map = {}
    for t_name in table_map:
        t_norm = _normalize_name(t_name)
        table_norm_map[t_name] = {t_norm, _singularize(t_norm), t_norm + "s"}

    pk_map = {}
    col_name_sets = {}
    for t_name, t in table_map.items():
        cols = t.get("columns", [])
        pk_map[t_name] = _find_pk(cols, t_name)
        col_name_sets[t_name] = {_to_text(c.get("name")) for c in cols}

    suggestions: List[Dict[str, Any]] = []
    seen = set()
    for child_name, t in table_map.items():
        for col in t.get("columns", []):
            col_name = _to_text(col.get("name"))
            norm_col = _normalize_name(col_name)
            if not norm_col.endswith("id"):
                continue

            is_optional = bool(col.get("is_nullable", True))
            entity = norm_col[:-2]
            if not entity:
                continue

            for parent_name, norm_variants in table_norm_map.items():
                if parent_name == child_name:
                    continue
                if entity not in norm_variants:
                    continue

                parent_pk = pk_map.get(parent_name)
                if not parent_pk:
                    continue

                if parent_pk not in col_name_sets[parent_name]:
                    continue

                key = (child_name, col_name, parent_name, parent_pk)
                if key in seen:
                    continue
                seen.add(key)

                suggestions.append(
                    {
                        "from_table": child_name,
                        "from_column": col_name,
                        "to_table": parent_name,
                        "to_column": parent_pk,
                        "cardinality": "1:N",
                        "is_optional": is_optional,
                        "confidence": 0.62,
                        "reason": "heuristic: *_id column matches parent table name",
                    }
                )
    return suggestions


def _build_prompt(tables: List[Dict[str, Any]]) -> str:
    compact = []
    for t in tables:
        compact.append(
            {
                "table_name": _to_text(t.get("name")),
                "columns": [
                    {
                        "name": _to_text(c.get("name")),
                        "data_type": _to_text(c.get("data_type")),
                        "is_pk": bool(c.get("is_pk", False)),
                        "is_nullable": bool(c.get("is_nullable", True)),
                    }
                    for c in t.get("columns", [])
                ],
            }
        )
    return (
        "Infer likely foreign-key relationships across these tables.\n"
        "Return STRICT JSON only in this shape:\n"
        "{\n"
        '  "relationships": [\n'
        "    {\n"
        '      "from_table": "...",\n'
        '      "from_column": "...",\n'
        '      "to_table": "...",\n'
        '      "to_column": "...",\n'
        '      "cardinality": "1:N|1:1|N:1|N:N",\n'
        '      "is_optional": true,\n'
        '      "confidence": 0.0,\n'
        '      "reason": "short reason"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- Use only provided tables/columns.\n"
        "- Prefer high precision.\n"
        "- Do not invent columns.\n"
        "- Output valid JSON only, no markdown.\n"
        f"Tables: {json.dumps(compact, default=str)}"
    )


def _validate_model_suggestions(
    tables: List[Dict[str, Any]], raw_rels: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    table_names = {_to_text(t.get("name")) for t in tables}
    cols_by_table = {
        _to_text(t.get("name")): {_to_text(c.get("name")) for c in t.get("columns", [])} for t in tables
    }
    valid: List[Dict[str, Any]] = []
    seen = set()

    for rel in raw_rels:
        ft = _to_text(rel.get("from_table"))
        fc = _to_text(rel.get("from_column"))
        tt = _to_text(rel.get("to_table"))
        tc = _to_text(rel.get("to_column"))
        if ft not in table_names or tt not in table_names:
            continue
        if fc not in cols_by_table.get(ft, set()) or tc not in cols_by_table.get(tt, set()):
            continue
        if ft == tt and fc == tc:
            continue
        key = (ft, fc, tt, tc)
        if key in seen:
            continue
        seen.add(key)

        card = _to_text(rel.get("cardinality") or "1:N").upper()
        if card not in {"1:N", "1:1", "N:1", "N:N"}:
            card = "1:N"
        conf = rel.get("confidence", 0.6)
        try:
            conf = float(conf)
        except Exception:
            conf = 0.6
        conf = max(0.0, min(1.0, conf))
        valid.append(
            {
                "from_table": ft,
                "from_column": fc,
                "to_table": tt,
                "to_column": tc,
                "cardinality": card,
                "is_optional": bool(rel.get("is_optional", True)),
                "confidence": conf,
                "reason": _to_text(rel.get("reason") or "inferred by model"),
            }
        )
    return valid


async def infer_table_relationships(tables: List[Dict[str, Any]]) -> Dict[str, Any]:
    heuristics = _heuristic_infer(tables)
    if len(tables) <= 1:
        return {"source": "heuristic", "model": None, "relationships": []}

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {
            "source": "heuristic",
            "model": None,
            "relationships": heuristics,
            "error": "GROQ_API_KEY not configured",
        }

    payload = {
        "model": GROQ_MODEL,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict JSON service for table relationship inference.",
            },
            {"role": "user", "content": _build_prompt(tables)},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        usage = extract_usage(data)
    except Exception as ex:
        return {
            "source": "heuristic",
            "model": None,
            "relationships": heuristics,
            "error": str(ex),
        }

    content = ""
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        pass

    parsed = _extract_json(content)
    if not parsed or "relationships" not in parsed:
        return {
            "source": "heuristic",
            "model": GROQ_MODEL,
            "relationships": heuristics,
            "error": "Invalid Groq response format",
        }

    validated = _validate_model_suggestions(tables, parsed.get("relationships", []))
    if not validated:
        return {
            "source": "heuristic",
            "model": GROQ_MODEL,
            "relationships": heuristics,
            "error": "No valid model suggestions",
        }

    return {"source": "groq", "model": GROQ_MODEL, "usage": usage, "relationships": validated}
