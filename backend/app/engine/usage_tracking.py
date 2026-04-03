import contextvars
import uuid
from typing import Any, Dict, Optional

from app.engine.metadata import get_db_connection

_ctx_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="anonymous")
_ctx_project_id: contextvars.ContextVar[str] = contextvars.ContextVar("project_id", default="")
_ctx_workflow_page: contextvars.ContextVar[str] = contextvars.ContextVar("workflow_page", default="")
_ctx_setup_mode: contextvars.ContextVar[str] = contextvars.ContextVar("setup_mode", default="")
_ctx_generation_kind: contextvars.ContextVar[str] = contextvars.ContextVar("generation_kind", default="")


def set_request_context(
    *,
    user_id: str = "anonymous",
    project_id: str = "",
    workflow_page: str = "",
    setup_mode: str = "",
    generation_kind: str = "",
) -> None:
    _ctx_user_id.set(str(user_id or "anonymous").strip() or "anonymous")
    _ctx_project_id.set(str(project_id or "").strip())
    _ctx_workflow_page.set(str(workflow_page or "").strip())
    _ctx_setup_mode.set(str(setup_mode or "").strip())
    _ctx_generation_kind.set(str(generation_kind or "").strip())


def clear_request_context() -> None:
    _ctx_user_id.set("anonymous")
    _ctx_project_id.set("")
    _ctx_workflow_page.set("")
    _ctx_setup_mode.set("")
    _ctx_generation_kind.set("")


def get_request_context() -> Dict[str, str]:
    return {
        "user_id": _ctx_user_id.get(),
        "project_id": _ctx_project_id.get(),
        "workflow_page": _ctx_workflow_page.get(),
        "setup_mode": _ctx_setup_mode.get(),
        "generation_kind": _ctx_generation_kind.get(),
    }


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def log_llm_usage_event(
    *,
    operation: str,
    model: Optional[str],
    source: str,
    status: str,
    usage: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> None:
    ctx = get_request_context()
    uid = str(user_id if user_id is not None else ctx.get("user_id") or "anonymous").strip() or "anonymous"
    pid = str(project_id if project_id is not None else ctx.get("project_id") or "").strip()
    usage = usage or {}
    prompt_tokens = _to_int(usage.get("prompt_tokens"), 0)
    completion_tokens = _to_int(usage.get("completion_tokens"), 0)
    total_tokens = _to_int(usage.get("total_tokens"), prompt_tokens + completion_tokens)

    conn = get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO llm_usage_events (
                id, user_id, project_id, operation, model, prompt_tokens, completion_tokens, total_tokens, source, status, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                uid,
                pid,
                str(operation or "").strip(),
                str(model or "").strip() or None,
                int(prompt_tokens),
                int(completion_tokens),
                int(total_tokens),
                str(source or "").strip(),
                str(status or "").strip(),
                str(error_message or "").strip() or None,
            ),
        )
    finally:
        conn.close()
