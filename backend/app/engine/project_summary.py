import os
from pathlib import Path
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv

load_dotenv()
repo_env = Path(__file__).resolve().parents[3] / ".env"
if repo_env.exists():
    load_dotenv(repo_env)

GROQ_API_URL = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = os.getenv("GROQ_SUMMARY_MODEL", os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"))


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _fallback_summary(project: Dict[str, Any], tables: List[Dict[str, Any]], relations: List[Dict[str, Any]]) -> str:
    table_names = [t.get("name", "unknown_table") for t in tables]
    total_cols = sum(len(t.get("columns", [])) for t in tables)
    if table_names:
        names_text = ", ".join(table_names[:4])
        if len(table_names) > 4:
            names_text += ", ..."
    else:
        names_text = "none"

    source = _to_text(project.get("source_type") or "unknown")
    return (
        f"{len(tables)} tables ({names_text}), {total_cols} columns, "
        f"{len(relations)} relations; source={source}. "
        "Typical relational synthetic-data project."
    )


def _build_prompt(project: Dict[str, Any], tables: List[Dict[str, Any]], relations: List[Dict[str, Any]]) -> str:
    compact_tables = []
    for table in tables:
        compact_tables.append(
            {
                "name": _to_text(table.get("name")),
                "row_count": table.get("row_count"),
                "columns": [
                    {
                        "name": _to_text(col.get("name")),
                        "data_type": _to_text(col.get("data_type")),
                        "is_pk": bool(col.get("is_pk", False)),
                        "is_pii": bool(col.get("is_pii", False)),
                    }
                    for col in table.get("columns", [])
                ],
            }
        )

    compact_relations = [
        {
            "from_table": _to_text(r.get("from_table")),
            "from_column": _to_text(r.get("from_column")),
            "to_table": _to_text(r.get("to_table")),
            "to_column": _to_text(r.get("to_column")),
        }
        for r in relations
    ]

    return (
        "Generate one short natural-language schema summary for a synthetic data project.\n"
        "Requirements:\n"
        "- 1 sentence only.\n"
        "- Under 35 words.\n"
        "- Mention number of tables, main entity names, and rough domain guess.\n"
        "- No markdown.\n"
        "- No quotes.\n"
        f"Project: {{'name':'{_to_text(project.get('name'))}','source_type':'{_to_text(project.get('source_type'))}'}}\n"
        f"Tables: {compact_tables}\n"
        f"Relations: {compact_relations}\n"
    )


async def infer_project_summary(
    project: Dict[str, Any], tables: List[Dict[str, Any]], relations: List[Dict[str, Any]]
) -> Dict[str, Any]:
    if not tables:
        return {"summary": "No tables detected yet.", "source": "heuristic", "model": None}

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {
            "summary": _fallback_summary(project, tables, relations),
            "source": "heuristic",
            "model": None,
            "error": "GROQ_API_KEY not configured",
        }

    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": "You generate concise schema summaries for data engineering workflows.",
            },
            {"role": "user", "content": _build_prompt(project, tables, relations)},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
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

        summary = _to_text(data["choices"][0]["message"]["content"]).strip()
        if not summary:
            raise ValueError("Empty model summary")

        summary = " ".join(summary.split())
        if len(summary) > 280:
            summary = summary[:277].rstrip() + "..."

        return {"summary": summary, "source": "groq", "model": GROQ_MODEL}
    except Exception as ex:
        return {
            "summary": _fallback_summary(project, tables, relations),
            "source": "heuristic",
            "model": GROQ_MODEL,
            "error": str(ex),
        }
