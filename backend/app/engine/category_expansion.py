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
GROQ_MODEL = os.getenv("GROQ_EXPANSION_MODEL", os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"))

LOCAL_CATEGORY_FAMILIES = [
    {
        "name": "gender",
        "column_hints": {"gender", "sex"},
        "values": ["Male", "Female", "Non-binary", "Prefer not to say"],
        "aliases": {
            "m": "Male",
            "male": "Male",
            "f": "Female",
            "female": "Female",
            "nonbinary": "Non-binary",
            "non-binary": "Non-binary",
            "other": "Non-binary",
            "others": "Non-binary",
            "prefer not to say": "Prefer not to say",
        },
    },
    {
        "name": "education_level",
        "column_hints": {"education", "education_level", "qualification", "degree"},
        "values": ["High School", "Associate", "Bachelor", "Master", "Doctorate"],
        "aliases": {
            "high school": "High School",
            "secondary": "High School",
            "associate": "Associate",
            "bachelor": "Bachelor",
            "bachelors": "Bachelor",
            "master": "Master",
            "masters": "Master",
            "doctorate": "Doctorate",
            "phd": "Doctorate",
        },
    },
    {
        "name": "marital_status",
        "column_hints": {"marital_status", "marital"},
        "values": ["Single", "Married", "Divorced", "Widowed"],
        "aliases": {},
    },
    {
        "name": "employment_status",
        "column_hints": {"employment_status", "employment", "job_status"},
        "values": ["Full-time", "Part-time", "Contract", "Unemployed"],
        "aliases": {
            "full time": "Full-time",
            "part time": "Part-time",
        },
    },
    {
        "name": "status",
        "column_hints": {"status", "state"},
        "values": ["Active", "Inactive", "Pending", "Suspended"],
        "aliases": {},
    },
    {
        "name": "priority",
        "column_hints": {"priority", "severity"},
        "values": ["Low", "Medium", "High", "Critical"],
        "aliases": {},
    },
    {
        "name": "product_category",
        "column_hints": {"product_category", "category", "product_type"},
        "values": ["Electronics", "Fashion", "Grocery", "Furniture", "Home", "Sports"],
        "aliases": {},
    },
]


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def normalize_allowed_values(raw: Any) -> str:
    if isinstance(raw, list):
        values = [str(v).strip() for v in raw if str(v).strip()]
        return ", ".join(dict.fromkeys(values))

    text = _to_text(raw).strip()
    if not text:
        return ""

    parts = [p.strip() for p in re.split(r"[\n,;]+", text) if p.strip()]
    return ", ".join(dict.fromkeys(parts))


def parse_allowed_values(raw: Any) -> List[str]:
    normalized = normalize_allowed_values(raw)
    if not normalized:
        return []
    return [part.strip() for part in normalized.split(",") if part.strip()]


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


def _heuristic_expand(column_name: str, seed_values: List[str], max_values: int) -> List[str]:
    if not seed_values:
        return []

    normalized_seeds = [_normalize_token(v) for v in seed_values]
    seed_set = {v for v in normalized_seeds if v}

    best_family = None
    best_score = 0
    normalized_column_name = _normalize_token(column_name)
    for family in LOCAL_CATEGORY_FAMILIES:
        family_tokens = {_normalize_token(v) for v in family["values"]}
        family_tokens.update(family["aliases"].keys())

        overlap = len(seed_set & family_tokens)
        hint_score = 0
        if any(_normalize_token(hint) in normalized_column_name for hint in family["column_hints"]):
            hint_score = 2
        score = overlap + hint_score
        if score > best_score:
            best_score = score
            best_family = family

    if not best_family or best_score <= 0:
        return seed_values[:max_values]

    normalized_lookup = {}
    for value in best_family["values"]:
        normalized_lookup[_normalize_token(value)] = value
    for alias, canonical in best_family["aliases"].items():
        normalized_lookup[_normalize_token(alias)] = canonical

    ordered = []
    seen = set()
    for seed in seed_values:
        canonical = normalized_lookup.get(_normalize_token(seed), seed)
        if canonical not in seen:
            seen.add(canonical)
            ordered.append(canonical)

    for value in best_family["values"]:
        if value not in seen:
            seen.add(value)
            ordered.append(value)

    return ordered[:max_values]


def _build_prompt(column: Dict[str, Any], seed_values: List[str], max_values: int) -> str:
    return (
        "Expand a small seed list of categorical values into a realistic synthetic-data vocabulary.\n"
        "Return STRICT JSON only with this shape:\n"
        "{\n"
        '  "expanded_values": ["..."],\n'
        '  "reason": "short reason"\n'
        "}\n"
        "Rules:\n"
        "- Preserve all seed values exactly as given.\n"
        f"- Return between {len(seed_values)} and {max_values} total values.\n"
        "- Add only semantically related categories.\n"
        "- Prefer enterprise-safe, neutral values.\n"
        "- Do not include explanations outside JSON.\n"
        f"Column: {json.dumps(column, default=str)}\n"
        f"Seed values: {json.dumps(seed_values, default=str)}"
    )


def _validate_expansion(seed_values: List[str], expanded_values: Any, max_values: int) -> List[str]:
    seeds = [v for v in seed_values if v]
    normalized = parse_allowed_values(expanded_values)
    merged = list(dict.fromkeys(seeds + normalized))
    return merged[:max_values]


async def expand_categorical_column(
    column: Dict[str, Any],
    seed_values: List[str],
    max_values: int = 12,
) -> Dict[str, Any]:
    seed_values = [str(v).strip() for v in seed_values if str(v).strip()]
    if not seed_values:
        return {
            "source": "none",
            "model": None,
            "expanded_values": [],
            "reason": "No seed values provided",
        }

    if len(seed_values) >= max_values:
        return {
            "source": "seed",
            "model": None,
            "expanded_values": seed_values[:max_values],
            "reason": "Seed values already meet expansion size",
        }

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {
            "source": "local",
            "model": None,
            "expanded_values": _heuristic_expand(_to_text(column.get("column_name")), seed_values, max_values),
            "reason": "Local category expansion fallback",
            "error": "GROQ_API_KEY not configured",
        }

    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.3,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict JSON service for categorical value expansion.",
            },
            {
                "role": "user",
                "content": _build_prompt(column, seed_values, max_values),
            },
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
            "source": "local",
            "model": None,
            "expanded_values": _heuristic_expand(_to_text(column.get("column_name")), seed_values, max_values),
            "reason": "Local category expansion fallback after API error",
            "error": str(ex),
        }

    content = ""
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        pass

    parsed = _extract_json(content)
    if not parsed:
        return {
            "source": "local",
            "model": GROQ_MODEL,
            "expanded_values": _heuristic_expand(_to_text(column.get("column_name")), seed_values, max_values),
            "reason": "Local category expansion fallback after parse error",
            "error": "Invalid Groq response format",
        }

    expanded = _validate_expansion(seed_values, parsed.get("expanded_values", []), max_values)
    if not expanded:
        expanded = _heuristic_expand(_to_text(column.get("column_name")), seed_values, max_values)

    return {
        "source": "groq",
        "model": GROQ_MODEL,
        "expanded_values": expanded,
        "reason": _to_text(parsed.get("reason") or "Expanded by model"),
    }
