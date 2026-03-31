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
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
ALLOWED_GENERATORS = {"auto", "categorical", "integer", "numerical", "datetime"}


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_allowed_values(raw: Any) -> str:
    if isinstance(raw, list):
        values = [str(v).strip() for v in raw if str(v).strip()]
        return ", ".join(dict.fromkeys(values))

    text = _to_text(raw).strip()
    if not text:
        return ""

    parts = [p.strip() for p in re.split(r"[\n,;|]+", text) if p.strip()]
    return ", ".join(dict.fromkeys(parts))


def _safe_generator(generator_type: str, data_type: str) -> str:
    g = (generator_type or "").strip().lower()
    if g in ALLOWED_GENERATORS:
        return g

    dtype = (data_type or "").upper()
    if any(t in dtype for t in ["DATE", "TIME"]):
        return "datetime"
    if any(t in dtype for t in ["INT", "NUM", "DEC", "DOUBLE", "FLOAT", "REAL"]):
        return "numerical"
    return "categorical"


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


def _heuristic_inference(column: Dict[str, Any], reason_prefix: str = "heuristic") -> Dict[str, Any]:
    name = str(column.get("column_name", "")).lower()
    dtype = str(column.get("data_type", "")).upper()
    semantic_type = "unknown"
    is_pii = False

    if "email" in name:
        semantic_type = "email"
        is_pii = True
        generator_type = "categorical"
    elif "phone" in name or "mobile" in name or "contact" in name:
        semantic_type = "phone"
        is_pii = True
        generator_type = "categorical"
    elif "name" in name:
        semantic_type = "person_name"
        is_pii = True
        generator_type = "categorical"
    elif any(k in name for k in ["address", "street", "zipcode", "postal", "postcode"]):
        semantic_type = "address"
        is_pii = True
        generator_type = "categorical"
    elif any(k in name for k in ["amount", "amt", "price", "cost", "revenue", "income", "salary", "invoice"]):
        semantic_type = "currency"
        generator_type = "numerical"
    elif any(k in name for k in ["dob", "birth"]):
        semantic_type = "birth_date"
        is_pii = True
        generator_type = "datetime"
    elif any(k in name for k in ["date", "time", "timestamp", "created", "updated"]):
        semantic_type = "datetime"
        generator_type = "datetime"
    elif name.startswith("is_") or name.startswith("has_") or "flag" in name:
        semantic_type = "boolean"
        generator_type = "categorical"
    elif name.endswith("_id") or name == "id" or "uuid" in name:
        semantic_type = "identifier"
        generator_type = "categorical"
    elif any(t in dtype for t in ["DATE", "TIME"]):
        semantic_type = "datetime"
        generator_type = "datetime"
    elif any(t in dtype for t in ["INT", "NUM", "DEC", "DOUBLE", "FLOAT", "REAL"]):
        semantic_type = "numeric"
        generator_type = "numerical"
    else:
        semantic_type = "categorical"
        generator_type = "categorical"

    return {
        "column_id": _to_text(column.get("column_id")),
        "column_name": _to_text(column.get("column_name")),
        "table_name": _to_text(column.get("table_name")),
        "semantic_type": semantic_type,
        "generator_type": _safe_generator(generator_type, column.get("data_type", "")),
        "allowed_values": _normalize_allowed_values(column.get("allowed_values")),
        "is_pii": bool(is_pii),
        "confidence": 0.55,
        "reason": f"{reason_prefix}: name/type pattern match",
    }


def _fallback_all(columns: List[Dict[str, Any]], reason_prefix: str = "heuristic") -> List[Dict[str, Any]]:
    return [_heuristic_inference(c, reason_prefix=reason_prefix) for c in columns]


def _build_prompt(columns: List[Dict[str, Any]]) -> str:
    compact = [
        {
            "column_id": _to_text(c.get("column_id")),
            "table_name": _to_text(c.get("table_name")),
            "column_name": _to_text(c.get("column_name")),
            "data_type": _to_text(c.get("data_type", "")),
            "allowed_values": _normalize_allowed_values(c.get("allowed_values")),
            "cardinality": c.get("cardinality"),
            "null_count": c.get("null_count"),
        }
        for c in columns
    ]
    return (
        "Infer semantic meaning for each column and map it to generator strategy.\n"
        "Return STRICT JSON with this shape only:\n"
        "{\n"
        '  "suggestions": [\n'
        "    {\n"
        '      "column_id": "...",\n'
        '      "semantic_type": "email|phone|person_name|address|currency|identifier|datetime|numeric|categorical|unknown",\n'
        '      "generator_type": "categorical|numerical|datetime|auto",\n'
        '      "allowed_values": ["Value1", "Value2", "..."],\n'
        '      "is_pii": true,\n'
        '      "confidence": 0.0,\n'
        '      "reason": "short reason"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- Always return one suggestion per input column_id.\n"
        "- Use column names as primary signal, data_type as secondary.\n"
        "- If generator_type is categorical, optionally provide an array of 2-10 distinct 'allowed_values' strings.\n"
        "- Only output valid JSON. No markdown.\n"
        f"Input columns: {json.dumps(compact, default=str)}"
    )


async def infer_column_semantics(columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not columns:
        return {"source": "none", "model": None, "suggestions": []}

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {
            "source": "heuristic",
            "model": None,
            "suggestions": _fallback_all(columns, reason_prefix="heuristic-no-key"),
            "error": "GROQ_API_KEY not configured",
        }

    prompt = _build_prompt(columns)
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict JSON service for schema semantic inference. "
                    "Respond with only JSON."
                ),
            },
            {"role": "user", "content": prompt},
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
            "suggestions": _fallback_all(columns, reason_prefix="heuristic-api-error"),
            "error": str(ex),
        }

    content = ""
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        pass

    parsed = _extract_json(content)
    if not parsed or "suggestions" not in parsed:
        return {
            "source": "heuristic",
            "model": GROQ_MODEL,
            "suggestions": _fallback_all(columns, reason_prefix="heuristic-parse-error"),
            "error": "Invalid Groq response format",
        }

    by_id = {_to_text(c.get("column_id")): c for c in columns}
    final_suggestions: List[Dict[str, Any]] = []
    seen_ids = set()

    for suggestion in parsed.get("suggestions", []):
        col_id = _to_text(suggestion.get("column_id"))
        if col_id not in by_id:
            continue
        col = by_id[col_id]
        seen_ids.add(col_id)
        conf = suggestion.get("confidence", 0.5)
        try:
            conf = float(conf)
        except Exception:
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        
        allowed_str = _normalize_allowed_values(suggestion.get("allowed_values"))
        normalized_generator = _safe_generator(suggestion.get("generator_type", "auto"), col.get("data_type", ""))
        if not allowed_str:
            allowed_str = _normalize_allowed_values(col.get("allowed_values"))

        final_suggestions.append(
            {
                "column_id": col_id,
                "column_name": col["column_name"],
                "table_name": col["table_name"],
                "semantic_type": suggestion.get("semantic_type", "unknown"),
                "generator_type": normalized_generator,
                "allowed_values": allowed_str,
                "is_pii": bool(suggestion.get("is_pii", False)),
                "confidence": conf,
                "reason": str(suggestion.get("reason", "inferred by model")),
            }
        )

    for col in columns:
        if _to_text(col.get("column_id")) not in seen_ids:
            final_suggestions.append(_heuristic_inference(col, reason_prefix="heuristic-missing-column"))

    return {
        "source": "groq",
        "model": GROQ_MODEL,
        "usage": usage,
        "suggestions": final_suggestions,
    }
