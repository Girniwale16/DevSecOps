import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

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


async def infer_column_associations(table_name: str, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not columns:
        return {"source": "none", "model": None, "associations": []}

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {
            "source": "none",
            "model": None,
            "associations": [],
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
    except Exception as ex:
        return {
            "source": "none",
            "model": None,
            "associations": [],
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
            "source": "none",
            "model": GROQ_MODEL,
            "associations": [],
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

    return {"source": "groq", "model": GROQ_MODEL, "associations": cleaned}
