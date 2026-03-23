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
GROQ_MODEL = os.getenv("GROQ_ASSISTANT_MODEL", os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"))

VALID_PAGES = {"upload", "input", "project", "modeling", "generate"}
VALID_SETUP_MODES = {"csv", "schema"}
VALID_OPERATIONS = {"refresh_summary", "refresh_plan", "infer_semantics", "launch_generation"}


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _compact_project_context(project: Optional[Dict[str, Any]], tables: List[Dict[str, Any]], relations: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "project": {
            "name": _to_text((project or {}).get("name") or "unsaved project"),
            "source_type": _to_text((project or {}).get("source_type") or "unknown"),
        },
        "table_count": len(tables),
        "tables": [
            {
                "name": _to_text(t.get("name")),
                "column_count": len(t.get("columns", [])),
                "columns": [
                    {
                        "name": _to_text(c.get("name")),
                        "data_type": _to_text(c.get("data_type")),
                        "is_pii": bool(c.get("is_pii", False)),
                    }
                    for c in t.get("columns", [])[:12]
                ],
            }
            for t in tables[:8]
        ],
        "relation_count": len(relations),
    }


def _next_valid_step(*, current_page: str, has_project: bool) -> Optional[str]:
    transitions = {
        "upload": "input",
        "input": "project",
        "project": "modeling",
        "modeling": "generate",
    }
    next_target = transitions.get(current_page)
    if next_target == "project" and not has_project:
        return None
    if next_target in {"modeling", "generate"} and not has_project:
        return None
    return next_target


def _heuristic_action(message: str, *, current_page: str, setup_mode: str, has_project: bool) -> Dict[str, Any]:
    text = _to_text(message).strip().lower()
    action = {"setup_mode": None, "target_page": None, "operation": None}
    normalized = text.replace("-", " ")
    mentions_file_input = any(token in normalized for token in ["csv", "file", "files", "upload"])
    asks_for_schema_start = any(
        token in normalized
        for token in [
            "generate synthetic data",
            "synthetic data",
            "create synthetic data",
            "create a schema project",
            "schema project",
        ]
    )
    asks_where_to_upload = any(
        token in normalized
        for token in [
            "where should i upload",
            "where do i upload",
            "where can i upload",
            "how do i upload",
            "upload where",
        ]
    )

    if not has_project and asks_for_schema_start and not mentions_file_input:
        action["setup_mode"] = "schema"
        action["target_page"] = "input"
        return action

    if "csv" in normalized:
        action["setup_mode"] = "csv"
    elif any(token in normalized for token in ["schema studio", "manual schema", "schema mode", "use schema", "schema builder"]):
        action["setup_mode"] = "schema"

    if any(token in normalized for token in ["go to setup", "back to setup", "setup page", "return to setup"]):
        action["target_page"] = "upload"
    elif any(token in normalized for token in ["go to input", "continue to input", "input page", "open input"]):
        action["target_page"] = "input"
    elif any(token in normalized for token in ["workspace", "project page", "open project", "open workspace", "go to workspace"]):
        action["target_page"] = "project"
    elif any(token in normalized for token in ["modeling", "modelling", "model page", "go to modeling", "open modeling"]):
        action["target_page"] = "modeling"
    elif any(token in normalized for token in ["generate", "generation page", "go generate", "launch stage", "open generate"]):
        action["target_page"] = "generate"

    if any(token in normalized for token in ["skip modeling", "skip model", "skip this modelling step", "skip this modeling step"]):
        action["target_page"] = "generate" if has_project else _next_valid_step(current_page=current_page, has_project=has_project)

    if "refresh summary" in normalized:
        action["operation"] = "refresh_summary"
    elif "refresh plan" in normalized:
        action["operation"] = "refresh_plan"
    elif any(token in normalized for token in ["infer semantics", "analyze columns", "analyse columns", "semantic inference"]):
        action["operation"] = "infer_semantics"
    elif any(token in normalized for token in ["launch generation", "start generation", "run generation", "generate data", "start run"]):
        action["operation"] = "launch_generation"

    if not action["target_page"] and action["setup_mode"] and current_page == "upload":
        action["target_page"] = "input"
    if asks_where_to_upload and current_page == "upload" and setup_mode == "csv":
        action["target_page"] = "input"
    if not action["target_page"] and any(token in normalized for token in ["next", "continue", "proceed", "move ahead", "move forward", "skip step"]):
        action["target_page"] = _next_valid_step(current_page=current_page, has_project=has_project)
    if not has_project and (action["target_page"] in {"project", "modeling", "generate"} or action["operation"] == "launch_generation"):
        if asks_for_schema_start and not mentions_file_input:
            action["setup_mode"] = "schema"
            action["target_page"] = "input"
            action["operation"] = None
    return action


def _fallback_reply(message: str, *, current_page: str, setup_mode: str, has_project: bool, action: Dict[str, Any], project_context: Dict[str, Any]) -> str:
    page_labels = {
        "upload": "Setup",
        "input": "Input",
        "project": "Workspace",
        "modeling": "Modeling",
        "generate": "Generate",
    }
    current_label = page_labels.get(current_page, current_page.title())
    setup_text = setup_mode.upper() if setup_mode else "not selected"
    next_step = _next_valid_step(current_page=current_page, has_project=has_project)
    if action.get("setup_mode") or action.get("target_page") or action.get("operation"):
        parts = [f"You are on {current_label}."]
        if action.get("setup_mode"):
            parts.append(f"I understood that you want to use {str(action['setup_mode']).upper()} mode.")
        if action.get("target_page"):
            parts.append(f"I will move the workflow toward {page_labels.get(str(action['target_page']), str(action['target_page']).title())}.")
        if action.get("operation"):
            parts.append(f"I will also trigger {str(action['operation']).replace('_', ' ')} if the current state allows it.")
        return " ".join(parts)

    if has_project:
        table_count = int(project_context.get("table_count", 0))
        relation_count = int(project_context.get("relation_count", 0))
        return (
            f"You are on {current_label} with setup mode {setup_text}. "
            f"The current project has {table_count} table(s) and {relation_count} relationship(s). "
            f"If you want to stay on workflow, the next step is {page_labels.get(next_step, current_label)}."
        )
    return (
        f"You are on {current_label} and the setup mode is {setup_text}. "
        f"If your request is outside the workflow, I will keep you on track. The next step is {page_labels.get(next_step, 'Setup')}."
    )


def _build_prompt(
    *,
    current_page: str,
    setup_mode: str,
    has_project: bool,
    project_context: Dict[str, Any],
    history: List[Dict[str, str]],
    message: str,
    heuristic_action: Dict[str, Any],
) -> str:
    return (
        "You are an in-app workflow assistant for a synthetic data application.\n"
        "Answer the user's question briefly and help them progress through the app.\n"
        "You must return strict JSON only with this schema:\n"
        '{'
        '"reply":"short plain-text answer",'
        '"action":{"setup_mode":null|\"csv\"|\"schema\",'
        '"target_page":null|\"upload\"|\"input\"|\"project\"|\"modeling\"|\"generate\",'
        '"operation":null|\"refresh_summary\"|\"refresh_plan\"|\"infer_semantics\"|\"launch_generation\"}'
        '}\n'
        "Rules:\n"
        "- Use action only when the user clearly asks to change workflow state or run something.\n"
        "- Treat words like next, continue, proceed, move ahead, open workspace, open modeling, and launch as workflow intents.\n"
        "- If the user asks to generate synthetic data but no project or files exist yet, send them to Schema Studio input first.\n"
        "- If the user asks for a schema project with columns, keep them on Input so they can fill in schema details.\n"
        "- If the user asks where to upload a CSV while on Setup with CSV selected, move them to Input because the uploader lives there.\n"
        "- If the user asks to skip a step, move only to the next valid workflow step.\n"
        "- If the request is unclear or outside the workflow, reply with the next workflow step instead of inventing a side task.\n"
        "- If the user asks a question, answer it directly in reply.\n"
        "- Keep reply under 90 words.\n"
        "- Do not mention JSON.\n"
        f"Current page: {current_page}\n"
        f"Current setup mode: {setup_mode or 'none'}\n"
        f"Has project: {has_project}\n"
        f"Project context: {project_context}\n"
        f"Recent chat history: {history[-8:]}\n"
        f"Heuristic action guess: {heuristic_action}\n"
        f"User message: {message}\n"
    )


def _sanitize_action(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = raw or {}
    action = {
        "setup_mode": _to_text(raw.get("setup_mode")).strip().lower() or None,
        "target_page": _to_text(raw.get("target_page")).strip().lower() or None,
        "operation": _to_text(raw.get("operation")).strip().lower() or None,
    }
    if action["setup_mode"] not in VALID_SETUP_MODES:
        action["setup_mode"] = None
    if action["target_page"] not in VALID_PAGES:
        action["target_page"] = None
    if action["operation"] not in VALID_OPERATIONS:
        action["operation"] = None
    return action


def _normalize_action_for_workflow(action: Dict[str, Any], *, current_page: str, has_project: bool) -> Dict[str, Any]:
    normalized = dict(action or {})
    if normalized.get("setup_mode") and current_page == "upload" and not normalized.get("target_page"):
        normalized["target_page"] = "input"
    if not has_project and normalized.get("target_page") in {"project", "modeling", "generate"}:
        normalized["target_page"] = _next_valid_step(current_page=current_page, has_project=has_project)
    return normalized


async def infer_assistant_reply(
    *,
    current_page: str,
    setup_mode: str,
    has_project: bool,
    project: Optional[Dict[str, Any]],
    tables: List[Dict[str, Any]],
    relations: List[Dict[str, Any]],
    history: List[Dict[str, str]],
    message: str,
) -> Dict[str, Any]:
    project_context = _compact_project_context(project, tables, relations)
    heuristic_action = _heuristic_action(
        message,
        current_page=current_page,
        setup_mode=setup_mode,
        has_project=has_project,
    )

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {
            "reply": _fallback_reply(
                message,
                current_page=current_page,
                setup_mode=setup_mode,
                has_project=has_project,
                action=heuristic_action,
                project_context=project_context,
            ),
            "action": heuristic_action,
            "source": "heuristic",
            "model": None,
            "error": "GROQ_API_KEY not configured",
        }

    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "You are a strict JSON assistant for an app workflow."},
            {
                "role": "user",
                "content": _build_prompt(
                    current_page=current_page,
                    setup_mode=setup_mode,
                    has_project=has_project,
                    project_context=project_context,
                    history=history,
                    message=message,
                    heuristic_action=heuristic_action,
                ),
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
        content = _to_text(data["choices"][0]["message"]["content"]).strip()
        parsed = _safe_json_loads(content) or {}
        reply = _to_text(parsed.get("reply")).strip()
        action = _normalize_action_for_workflow(
            _sanitize_action(parsed.get("action")),
            current_page=current_page,
            has_project=has_project,
        )
        if not reply:
            raise ValueError("Empty assistant reply")
        if not any(action.values()):
            action = heuristic_action
        return {"reply": reply, "action": action, "source": "groq", "model": GROQ_MODEL}
    except Exception as ex:
        return {
            "reply": _fallback_reply(
                message,
                current_page=current_page,
                setup_mode=setup_mode,
                has_project=has_project,
                action=heuristic_action,
                project_context=project_context,
            ),
            "action": heuristic_action,
            "source": "heuristic",
            "model": GROQ_MODEL,
            "error": str(ex),
        }
