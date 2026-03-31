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


def _build_prompt(table_name: str, columns: List[Dict[str, Any]]) -> str:
    compact = [
        {
            "column_name": _to_text(c.get("column_name")),
            "data_type": _to_text(c.get("data_type")),
            "cardinality": c.get("cardinality"),
            "sample_values": c.get("sample_values", []),
        }
        for c in columns
    ]
    return (
        "Infer meaningful same-table associations between columns. "
        "Examples: city->state->country, product->product_category. "
        "Only return associations that are likely true based on names and sample values.\n"
        "Return STRICT JSON with this shape only:\n"
        "{\n"
        '  "associations": [\n'
        "    {\n"
        '      "col_a": "column name",\n'
        '      "col_b": "column name",\n'
        '      "association": "hierarchy|category|geography|identifier|derived|other",\n'
        '      "confidence": 0.0,\n'
        '      "reason": "short reason"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- Only include same-table associations.\n"
        "- Use column names as primary signal, sample values as secondary.\n"
        "- Do not hallucinate unrelated links.\n"
        "- Only output valid JSON. No markdown.\n"
        f"Table: {table_name}\n"
        f"Columns: {json.dumps(compact, default=str)}"
    )


def _fallback_associations(columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    names = [str(c.get("column_name") or "").strip() for c in columns if str(c.get("column_name") or "").strip()]
    lowered = [n.lower() for n in names]
    rows: List[Dict[str, Any]] = []

    # Generic identifier links for *_id columns
    id_cols = [n for n in names if n.lower().endswith("_id")]
    for i in range(len(id_cols)):
        for j in range(i + 1, len(id_cols)):
            rows.append(
                {
                    "col_a": id_cols[i],
                    "col_b": id_cols[j],
                    "association": "identifier",
                    "confidence": 0.68,
                    "reason": "Heuristic fallback: both columns look like identifier fields.",
                }
            )

    def _has(col_key: str) -> Optional[str]:
        for n in names:
            if col_key in n.lower():
                return n
        return None

    # Common semantic pairs
    city = _has("city")
    state = _has("state")
    country = _has("country")
    if city and state:
        rows.append({"col_a": city, "col_b": state, "association": "geography", "confidence": 0.8, "reason": "Heuristic fallback: city/state often form a geographic hierarchy."})
    if state and country:
        rows.append({"col_a": state, "col_b": country, "association": "geography", "confidence": 0.8, "reason": "Heuristic fallback: state/country often form a geographic hierarchy."})
    if city and country:
        rows.append({"col_a": city, "col_b": country, "association": "geography", "confidence": 0.75, "reason": "Heuristic fallback: city/country are commonly related geographic columns."})

    price = _has("price")
    cost = _has("cost") or _has("cogs")
    if price and cost:
        rows.append({"col_a": price, "col_b": cost, "association": "derived", "confidence": 0.82, "reason": "Heuristic fallback: price and cost columns are commonly mathematically related."})

    category = _has("category")
    subcategory = _has("subcategory") or _has("sub_category")
    item_type = _has("type")
    if category and subcategory:
        rows.append({"col_a": category, "col_b": subcategory, "association": "hierarchy", "confidence": 0.85, "reason": "Heuristic fallback: category/subcategory names imply hierarchical grouping."})
    if category and item_type:
        rows.append({"col_a": category, "col_b": item_type, "association": "hierarchy", "confidence": 0.7, "reason": "Heuristic fallback: category/type names imply related grouping."})

    # De-duplicate preserving order
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for row in rows:
        key = tuple(sorted([str(row["col_a"]), str(row["col_b"])]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped[:12]


async def infer_column_associations(table_name: str, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not columns:
        return {"source": "none", "model": None, "associations": []}

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {
            "source": "heuristic",
            "model": None,
            "associations": _fallback_associations(columns),
            "error": "GROQ_API_KEY not configured",
        }

    prompt = _build_prompt(table_name, columns)
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "You are a strict JSON service for association inference."},
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
            "associations": _fallback_associations(columns),
            "error": str(ex),
        }

    content = ""
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        pass

    parsed = _extract_json(content)
    if not parsed or "associations" not in parsed:
        return {
            "source": "heuristic",
            "model": GROQ_MODEL,
            "associations": _fallback_associations(columns),
            "error": "Invalid Groq response format",
        }

    cleaned = []
    for row in parsed.get("associations", []):
        col_a = _to_text(row.get("col_a"))
        col_b = _to_text(row.get("col_b"))
        if not col_a or not col_b:
            continue
        confidence = row.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        cleaned.append(
            {
                "col_a": col_a,
                "col_b": col_b,
                "association": _to_text(row.get("association") or "other"),
                "confidence": confidence,
                "reason": _to_text(row.get("reason") or "inferred by model"),
            }
        )

    return {"source": "groq", "model": GROQ_MODEL, "usage": usage, "associations": cleaned}
