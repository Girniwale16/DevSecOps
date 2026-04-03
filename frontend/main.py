import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from nicegui import app, events, run as nicegui_run, ui
from auth import (
    build_auth_headers,
    clear_auth_state,
    get_auth_token,
    get_auth_user,
    is_authenticated,
    set_auth_state,
)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
UI_TIMEZONE = os.getenv("UI_TIMEZONE", "Asia/Kolkata")


def _patch_nicegui_process_pool_setup() -> None:
    original_setup = nicegui_run.setup

    def safe_setup() -> None:
        try:
            original_setup()
        except (NotImplementedError, PermissionError, OSError) as exc:
            logging.warning("NiceGUI process pool disabled: %s", exc)
            nicegui_run.process_pool = None

    nicegui_run.setup = safe_setup


_patch_nicegui_process_pool_setup()

STEPS = [
    {"key": "upload", "label": "Setup"},
    {"key": "input", "label": "Input"},
    {"key": "project", "label": "Workspace"},
    {"key": "modeling", "label": "Modeling"},
    {"key": "generate", "label": "Generate"},
    {"key": "output", "label": "Output"},
]

ui.add_head_html(
    """
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <style>
      :root {
        --nexus-bg-a: #f3fbff;
        --nexus-bg-b: #fff8eb;
        --nexus-brand: #0b3a53;
        --nexus-accent: #ea580c;
        --nexus-soft: #d8e9f6;
        --nexus-ink: #183348;
      }
      body {
        font-family: 'Sora', sans-serif;
        color: var(--nexus-ink);
        background:
          radial-gradient(circle at 10% 8%, rgba(255, 183, 77, 0.18), transparent 28%),
          radial-gradient(circle at 88% 92%, rgba(14, 165, 233, 0.18), transparent 28%),
          linear-gradient(135deg, var(--nexus-bg-a), var(--nexus-bg-b));
      }
      .glass-panel {
        background: rgba(255, 255, 255, 0.88);
        border: 1px solid rgba(148, 182, 206, 0.4);
        border-radius: 16px;
        backdrop-filter: blur(8px);
      }
      .stage-pill {
        border: 1px solid #bfd5e7;
        border-radius: 9999px;
        transition: all 0.22s ease;
      }
      .stage-pill.active {
        background: var(--nexus-brand);
        color: #ffffff;
        border-color: var(--nexus-brand);
        box-shadow: 0 10px 22px rgba(11, 58, 83, 0.2);
      }
      .lift {
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      }
      .lift:hover {
        transform: translateY(-2px);
        box-shadow: 0 18px 36px rgba(11, 58, 83, 0.12);
      }
      .fade-up {
        animation: fadeUp 0.35s ease;
      }
      .mono {
        font-family: 'JetBrains Mono', monospace;
      }
      .upload-mode-card {
        min-height: 430px;
      }
      .upload-zone .q-uploader {
        border: 1px dashed #b8d2e5;
        border-radius: 12px;
        background: rgba(255, 255, 255, 0.8);
      }
      .upload-zone .q-uploader__header {
        border-radius: 12px 12px 0 0;
      }
      .setup-section-title {
        letter-spacing: 0.02em;
      }
      .setup-stats {
        display: flex;
        gap: 0.5rem;
        flex-wrap: wrap;
        margin-top: 0.65rem;
      }
      .setup-chip {
        border: 1px solid #dbe7f2;
        background: #f8fbff;
        color: #355065;
        font-size: 0.72rem;
        font-weight: 700;
        border-radius: 9999px;
        padding: 0.22rem 0.6rem;
      }
      .upload-file-shell {
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid #dbe7f2;
        border-radius: 12px;
      }
      .upload-table-head,
      .upload-table-row {
        display: grid;
        grid-template-columns: 2rem minmax(0, 3fr) 6rem 7rem 3rem;
        column-gap: 0.5rem;
        align-items: center;
      }
      .schema-table-item {
        border: 1px solid #e2e8f0;
        background: rgba(255, 255, 255, 0.72);
        border-radius: 12px;
        transition: border-color 0.2s ease, box-shadow 0.2s ease;
      }
      .schema-table-item.active {
        border-color: rgba(11, 58, 83, 0.45);
        box-shadow: 0 14px 28px rgba(11, 58, 83, 0.08);
      }
      .schema-meta-chip {
        border: 1px solid #e2e8f0;
        background: #f8fafc;
        color: #475569;
        border-radius: 9999px;
        font-size: 0.7rem;
        font-weight: 700;
        padding: 0.18rem 0.6rem;
      }
      .cell-truncate {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .assistant-shell {
        width: min(58vw, 62rem);
        min-width: 32rem;
        height: min(78vh, 56rem);
        max-height: calc(100vh - 3.5rem);
        border-radius: 22px;
        border: 1px solid rgba(148, 182, 206, 0.5);
        background: rgba(255, 255, 255, 0.95);
        backdrop-filter: blur(14px);
        box-shadow: 0 24px 60px rgba(15, 23, 42, 0.18);
        display: flex;
        flex-direction: column;
      }
      .assistant-shell.fullscreen {
        position: fixed !important;
        inset: 1rem !important;
        width: min(96vw, 92rem);
        height: calc(100vh - 2rem);
        max-height: calc(100vh - 2rem);
        min-width: 0;
        margin: 0 !important;
        z-index: 1200;
      }
      .assistant-header {
        background:
          radial-gradient(circle at top left, rgba(14, 165, 233, 0.18), transparent 36%),
          linear-gradient(135deg, rgba(11, 58, 83, 0.98), rgba(13, 94, 135, 0.94));
        color: #ffffff;
        border-radius: 22px 22px 0 0;
      }
      .assistant-bubble {
        max-width: 88%;
        border-radius: 18px;
        padding: 0.75rem 0.9rem;
        line-height: 1.45;
        font-size: 0.9rem;
      }
      .assistant-bubble.assistant {
        align-self: flex-start;
        background: #f7fafc;
        border: 1px solid #dbe7f2;
        color: #294256;
      }
      .assistant-bubble.user {
        align-self: flex-end;
        background: #0b3a53;
        color: #ffffff;
      }
      .assistant-chip {
        border-radius: 9999px;
        border: 1px solid rgba(255, 255, 255, 0.2);
        background: rgba(255, 255, 255, 0.12);
        font-size: 0.72rem;
        font-weight: 700;
        padding: 0.18rem 0.55rem;
      }
      .assistant-fab {
        width: 4rem;
        height: 4rem;
        border-radius: 9999px;
        background: linear-gradient(135deg, #0b3a53, #0ea5e9);
        color: #ffffff;
        box-shadow: 0 16px 36px rgba(14, 90, 133, 0.35);
      }
      .assistant-scroll {
        flex: 1 1 auto;
        min-height: 16rem;
      }
      .assistant-choice {
        border-radius: 9999px;
        border: 1px solid #bfd5e7;
        background: #f8fbff;
        color: var(--nexus-brand);
        font-weight: 700;
      }
      .admin-workflow-card {
        min-height: 100%;
      }
      .admin-step-chip {
        border: 1px solid #bfdbfe;
        background: #eff6ff;
        color: #1d4ed8;
        border-radius: 9999px;
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        padding: 0.22rem 0.62rem;
      }
      .admin-user-card {
        border: 1px solid #dbe7f2;
        background: rgba(255, 255, 255, 0.76);
        border-radius: 14px;
      }
      .admin-user-meta {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.9rem;
      }
      .admin-meta-label {
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #64748b;
      }
      .admin-meta-value {
        font-size: 0.95rem;
        color: #334155;
      }
      .admin-actions {
        border-top: 1px solid #e2e8f0;
      }
      .q-tooltip {
        white-space: nowrap !important;
      }
      @media (max-width: 768px) {
        .upload-mode-card {
          min-height: 0;
        }
        .upload-table-head,
        .upload-table-row {
          grid-template-columns: 1.8rem minmax(0, 2.6fr) 4.8rem 5.6rem 2.4rem;
          column-gap: 0.35rem;
        }
        .assistant-shell {
          width: calc(100vw - 1rem);
          min-width: 0;
          height: calc(100vh - 1rem);
        }
        .assistant-shell.fullscreen {
          inset: 0.25rem !important;
          width: calc(100vw - 0.5rem);
          height: calc(100vh - 0.5rem);
          max-height: calc(100vh - 0.5rem);
        }
        .assistant-scroll {
          max-height: calc(100vh - 16rem);
        }
        .admin-user-meta {
          grid-template-columns: 1fr;
        }
      }
      @keyframes fadeUp {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
      }
    </style>
    """,
    shared=True,
)


@ui.page("/")
async def main_page() -> None:
    client = ui.context.client
    stored_auth_token = get_auth_token(app.storage.user)
    stored_auth_user = get_auth_user(app.storage.user)
    if stored_auth_token:
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=build_auth_headers(stored_auth_token)) as auth_client:
                me_resp = await auth_client.get(f"{BACKEND_URL}/auth/me")
            if me_resp.is_success:
                stored_auth_user = (me_resp.json() or {}).get("user") or stored_auth_user
                if stored_auth_user:
                    set_auth_state(app.storage.user, stored_auth_token, stored_auth_user)
            else:
                clear_auth_state(app.storage.user)
                stored_auth_token = ""
                stored_auth_user = None
        except Exception:
            clear_auth_state(app.storage.user)
            stored_auth_token = ""
            stored_auth_user = None

    class APIError(Exception):
        def __init__(self, status_code: int, detail: Any):
            self.status_code = int(status_code)
            self.detail = detail
            super().__init__(f"HTTP {self.status_code}: {self.detail}")

    # Start each frontend load as a fresh session.
    app.storage.user["project_id"] = None
    app.storage.user["active_page"] = "upload"
    stored_project_id = None
    initial_page = "upload"

    local_state: Dict[str, Any] = {
        "page": initial_page,
        "project_id": stored_project_id,
        "project_data": None,
        "selected_table": None,
        "num_rows": 1000,
        "seed": 42,
        "selected_generation_table": None,
        "generation_table_settings": {},
        "stddev_scale": 1.0,
        "variation_pct": 0.0,
        "knn_smoothing": 0.0,
        "knn_neighbors": 5,
        "dialect": "postgres",
        "generation_plan": None,
        "output_format": "csv",
        "task_id": None,
        "task_status": "idle",
        "task_progress": 0,
        "task_logs": [],
        "task_file_url": None,
        "last_generation_kind": "",
        "sample_generated": False,
        "sample_confirmed": False,
        "sample_preview_tables": [],
        "sample_preview_error": None,
        "is_loading_project": False,
        "is_inferring_semantics": False,
        "is_detecting_pii": False,
        "is_expanding_categories": False,
        "project_summary": None,
        "is_loading_summary": False,

        "multi_csv_inflight": 0,
        "uploaded_tables": [],
        "setup_mode": "",
        "schema_project_name": "",
        "schema_tables": [],
        "schema_active_table_idx": 0,
        "is_submitting_schema": False,
        "editable_relations": [],
        "modeling_table_collapsed": {},
        "is_inferring_relations": False,
        "is_saving_relations": False,
        "correlation_rows": [],
        "correlation_note": None,
        "correlation_table_id": None,
        "is_loading_correlation": False,
        "association_rows": [],
        "association_note": None,
        "llm_association_rows": [],
        "llm_association_note": None,
        "llm_association_meta": None,
        "assistant_open": False,
        "assistant_chat": [],
        "assistant_input": "",
        "assistant_busy": False,
        "assistant_meta": None,
        "assistant_mode_active": False,
        "assistant_page": initial_page,
        "assistant_fullscreen": False,
        "assistant_fresh_start": False,
        "recent_csv_uploads": {},
        "pending_download_url": None,
        "auth_token": stored_auth_token,
        "auth_user": stored_auth_user,
        "login_username": stored_auth_user.get("username", "") if isinstance(stored_auth_user, dict) else "",
        "login_password": "",
        "login_error": "",
        "auth_busy": False,
        "admin_loading": False,
        "admin_search": "",
        "admin_users": [],
        "admin_audit": [],
        "admin_activity": [],
        "admin_create_username": "",
        "admin_create_password": "",
        "admin_create_confirm_password": "",
        "admin_create_role": "user",
        "admin_create_active": True,
        "profile_menu_open": False,
    }
    SCHEMA_TYPE_OPTIONS = [
        "varchar",
        "text",
        "integer",
        "bigint",
        "smallint",
        "decimal",
        "double",
        "float",
        "boolean",
        "date",
        "timestamp",
    ]
    TYPE_NORMALIZATION = {
        "string": "varchar",
        "str": "varchar",
        "varchar": "varchar",
        "character varying": "varchar",
        "char": "varchar",
        "text": "text",
        "int": "integer",
        "integer": "integer",
        "bigint": "bigint",
        "smallint": "smallint",
        "decimal": "decimal",
        "numeric": "decimal",
        "double": "double",
        "double precision": "double",
        "float": "float",
        "real": "float",
        "bool": "boolean",
        "boolean": "boolean",
        "date": "date",
        "datetime": "timestamp",
        "timestamp": "timestamp",
    }

    def stage_open(name: str) -> bool:
        if name == "upload":
            return True
        if name == "input":
            return bool(local_state["setup_mode"])
        if name == "output":
            return bool(local_state["project_id"]) and bool(local_state.get("sample_confirmed"))
        return bool(local_state["project_id"])

    def safe_notify(message: str, notify_type: str = "info") -> None:
        try:
            ui.notify(message, type=notify_type)
        except Exception:
            print(f"[notify:{notify_type}] {message}")

    def auth_headers() -> Dict[str, str]:
        return build_auth_headers(local_state.get("auth_token") or "")

    def api_client(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, headers=auth_headers())

    def auth_user() -> Dict[str, Any]:
        user = local_state.get("auth_user")
        return user if isinstance(user, dict) else {}

    def auth_role() -> str:
        return str(auth_user().get("role") or "").strip().lower()

    def is_super_admin_user() -> bool:
        return auth_role() == "super_admin"

    def is_admin_user() -> bool:
        return auth_role() in {"super_admin", "admin"}

    def format_timestamp(value: Any) -> str:
        if value in (None, ""):
            return "Never"
        try:
            target_tz = ZoneInfo(UI_TIMEZONE)
        except Exception:
            target_tz = timezone.utc

        try:
            if isinstance(value, (int, float)) or (str(value).strip().isdigit() and len(str(value).strip()) >= 10):
                dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
            else:
                text = str(value).strip()
                normalized = text.replace("Z", "+00:00")
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(target_tz).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            text = str(value or "").strip().replace("T", " ")
            return text[:19] if text else "Never"

    def clear_login_session(*, notify: bool = False) -> None:
        clear_auth_state(app.storage.user)
        local_state["auth_token"] = ""
        local_state["auth_user"] = None
        local_state["profile_menu_open"] = False
        local_state["login_password"] = ""
        local_state["auth_busy"] = False
        local_state["login_error"] = ""
        local_state["page"] = "upload"
        if notify:
            safe_notify("Your session has expired. Please sign in again.", notify_type="warning")

    def close_profile_menu() -> None:
        if local_state.get("profile_menu_open"):
            local_state["profile_menu_open"] = False
            safe_refresh(nav_bar)

    def toggle_profile_menu() -> None:
        local_state["profile_menu_open"] = not bool(local_state.get("profile_menu_open"))
        safe_refresh(nav_bar)

    async def submit_login() -> None:
        username = str(local_state.get("login_username") or "").strip()
        password = str(local_state.get("login_password") or "")
        if not username or not password:
            local_state["login_error"] = "Enter username and password."
            safe_refresh(login_view)
            return

        local_state["auth_busy"] = True
        local_state["login_error"] = ""
        safe_refresh(login_view)
        try:
            async with httpx.AsyncClient(timeout=15.0) as login_client:
                resp = await login_client.post(
                    f"{BACKEND_URL}/auth/login",
                    json={"username": username, "password": password},
                )
            data = await parse_response(resp)
            token = str(data.get("token") or "").strip()
            user = data.get("user") or {"username": username}
            if not token:
                raise APIError(500, "Login response did not include a token")
            set_auth_state(app.storage.user, token, user)
            local_state["auth_token"] = token
            local_state["auth_user"] = dict(user)
            local_state["login_password"] = ""
            safe_notify("Signed in successfully.", notify_type="positive")
            client.run_javascript("window.location.reload()")
        except Exception as ex:
            local_state["login_error"] = str(ex)
            safe_notify(f"Login failed: {ex}", notify_type="negative")
        finally:
            local_state["auth_busy"] = False
            safe_refresh(login_view)

    async def submit_logout() -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=auth_headers()) as logout_client:
                await logout_client.post(f"{BACKEND_URL}/auth/logout")
        except Exception:
            pass
        clear_login_session()
        safe_notify("Signed out.", notify_type="positive")
        client.run_javascript("window.location.reload()")

    async def load_admin_data() -> None:
        if not is_admin_user() or local_state.get("admin_loading"):
            return
        local_state["admin_loading"] = True
        safe_refresh(admin_view)
        try:
            async with api_client(timeout=20.0) as client_api:
                users_resp = await client_api.get(f"{BACKEND_URL}/auth/admin/users")
                audit_resp = await client_api.get(f"{BACKEND_URL}/auth/admin/audit", params={"limit": 8})
                activity_resp = await client_api.get(f"{BACKEND_URL}/auth/admin/activity", params={"limit": 20})
            user_payload = await parse_response(users_resp)
            audit_payload = await parse_response(audit_resp)
            activity_payload = await parse_response(activity_resp)
            local_state["admin_users"] = list(user_payload.get("users") or [])
            local_state["admin_audit"] = list(audit_payload.get("items") or [])
            local_state["admin_activity"] = list(activity_payload.get("items") or [])
        except Exception as ex:
            safe_notify(f"Admin data load failed: {ex}", notify_type="negative")
        finally:
            local_state["admin_loading"] = False
            safe_refresh(admin_view)
            safe_refresh(nav_bar)

    def clear_admin_create_form(*, refresh: bool = True) -> None:
        local_state["admin_create_username"] = ""
        local_state["admin_create_password"] = ""
        local_state["admin_create_confirm_password"] = ""
        local_state["admin_create_role"] = "user"
        local_state["admin_create_active"] = True
        if refresh:
            safe_refresh(admin_view)

    def assignable_roles() -> List[str]:
        if is_super_admin_user():
            return ["super_admin", "admin", "user"]
        return ["admin", "user"]

    def validate_password_rule(password: str, *, label: str = "Password") -> bool:
        clean = str(password or "")
        if len(clean) < 6:
            safe_notify(f"{label} must be at least 6 characters.", notify_type="warning")
            return False
        return True

    async def create_admin_user(dialog: Any = None) -> None:
        username = str(local_state.get("admin_create_username") or "").strip()
        password = str(local_state.get("admin_create_password") or "")
        confirm_password = str(local_state.get("admin_create_confirm_password") or "")
        role = str(local_state.get("admin_create_role") or "user")
        is_active = bool(local_state.get("admin_create_active"))
        if not username or not password:
            safe_notify("Username and password are required.", notify_type="warning")
            return
        if not validate_password_rule(password):
            return
        if password != confirm_password:
            safe_notify("Password and confirm password must match.", notify_type="warning")
            return
        try:
            async with api_client(timeout=20.0) as client_api:
                resp = await client_api.post(
                    f"{BACKEND_URL}/auth/admin/users",
                    json={
                        "username": username,
                        "password": password,
                        "role": role,
                        "is_active": is_active,
                    },
                )
            await parse_response(resp)
            safe_notify(f"Created user {username}.", notify_type="positive")
            clear_admin_create_form()
            if dialog is not None:
                dialog.close()
            await load_admin_data()
        except Exception as ex:
            safe_notify(f"Create user failed: {ex}", notify_type="negative")

    async def reset_admin_user_password(username: str, password: str, confirm_password: str, dialog: Any) -> None:
        if not password:
            safe_notify("Enter a new password.", notify_type="warning")
            return
        if not validate_password_rule(password, label="New password"):
            return
        if password != confirm_password:
            safe_notify("Password and confirm password must match.", notify_type="warning")
            return
        try:
            async with api_client(timeout=20.0) as client_api:
                resp = await client_api.post(
                    f"{BACKEND_URL}/auth/admin/users/{username}/reset-password",
                    json={"password": password},
                )
            await parse_response(resp)
            dialog.close()
            safe_notify(f"Password reset for {username}.", notify_type="positive")
            await load_admin_data()
        except Exception as ex:
            safe_notify(f"Password reset failed: {ex}", notify_type="negative")

    async def update_admin_user_role(username: str, role: str, dialog: Any) -> None:
        try:
            async with api_client(timeout=20.0) as client_api:
                resp = await client_api.post(
                    f"{BACKEND_URL}/auth/admin/users/{username}/role",
                    json={"role": role},
                )
            await parse_response(resp)
            dialog.close()
            safe_notify(f"Updated role for {username}.", notify_type="positive")
            await load_admin_data()
        except Exception as ex:
            safe_notify(f"Role change failed: {ex}", notify_type="negative")

    async def update_admin_user_status(username: str, is_active: bool) -> None:
        try:
            async with api_client(timeout=20.0) as client_api:
                resp = await client_api.post(
                    f"{BACKEND_URL}/auth/admin/users/{username}/status",
                    json={"is_active": is_active},
                )
            await parse_response(resp)
            safe_notify(f"{'Activated' if is_active else 'Deactivated'} {username}.", notify_type="positive")
            await load_admin_data()
        except Exception as ex:
            safe_notify(f"Status update failed: {ex}", notify_type="negative")

    async def delete_admin_user(username: str, dialog: Any) -> None:
        try:
            async with api_client(timeout=20.0) as client_api:
                resp = await client_api.delete(f"{BACKEND_URL}/auth/admin/users/{username}")
            await parse_response(resp)
            dialog.close()
            safe_notify(f"Deleted user {username}.", notify_type="positive")
            await load_admin_data()
        except Exception as ex:
            safe_notify(f"Delete user failed: {ex}", notify_type="negative")

    def download_file(url: str, *, notify: bool = True) -> bool:
        """Download a file from URL using browser's download mechanism."""
        if not url:
            if notify:
                safe_notify("Download URL is empty. File may not be ready.", notify_type="warning")
            return False
        
        try:
            print(f"[download] Browser-accessible URL: {url}")
            token = str(local_state.get("auth_token") or "").strip()
            auth_header = json.dumps(f"Bearer {token}") if token else "null"

            js = f"""
                (async () => {{
                    try {{
                        console.log('Downloading from:', '{url}');
                        
                        // Try fetch first to handle CORS
                        try {{
                            const authHeader = {auth_header};
                            const headers = authHeader ? {{ Authorization: authHeader }} : {{}};
                            const response = await fetch('{url}', {{ 
                                method: 'GET',
                                credentials: 'same-origin',
                                headers
                            }});
                            
                            if (response.ok) {{
                                const blob = await response.blob();
                                console.log('Blob received, size:', blob.size);
                                const disposition = response.headers.get('Content-Disposition') || response.headers.get('content-disposition') || '';
                                let filename = 'download';
                                const utf8Match = disposition.match(/filename\\*=UTF-8''([^;]+)/i);
                                const plainMatch = disposition.match(/filename="?([^\";]+)"?/i);
                                if (utf8Match && utf8Match[1]) {{
                                    filename = decodeURIComponent(utf8Match[1]);
                                }} else if (plainMatch && plainMatch[1]) {{
                                    filename = plainMatch[1];
                                }}
                                const downloadUrl = window.URL.createObjectURL(blob);
                                const link = document.createElement('a');
                                link.href = downloadUrl;
                                link.download = filename;
                                document.body.appendChild(link);
                                link.click();
                                document.body.removeChild(link);
                                window.URL.revokeObjectURL(downloadUrl);
                                console.log('Download completed successfully');
                                return;
                            }}
                        }} catch (fetchError) {{
                            console.log('Fetch failed:', fetchError.message);
                        }}
                        throw new Error('Authenticated download failed');
                        
                    }} catch (error) {{
                        console.error('Download error:', error);
                        alert('Download failed: ' + error.message);
                    }}
                }})();
            """

            # If this function is called from a background task, defer to next UI render cycle.
            if not client_is_active():
                local_state["pending_download_url"] = url
                return False

            try:
                client.run_javascript(js)
            except Exception:
                # Fallback for cases where client runner is unavailable
                ui.run_javascript(js)

            if notify:
                safe_notify("Download started. Check your downloads folder.", notify_type="positive")
            return True
        except Exception as ex:
            if notify:
                safe_notify(f"Download error: {ex}", notify_type="negative")
            print(f"[download] Exception: {ex}")
            return False

    def flush_pending_download() -> None:
        pending_url = str(local_state.get("pending_download_url") or "").strip()
        if not pending_url:
            return
        # Clear first to avoid duplicate downloads on repeated refreshes.
        local_state["pending_download_url"] = None
        download_file(pending_url, notify=False)

    def handle_download_click() -> None:
        """Handle download button click with proper error checking."""
        file_url = local_state.get("task_file_url")
        if not file_url:
            safe_notify("Download URL not available. Please try generating again.", notify_type="warning")
            return
        if local_state.get("task_status") != "done":
            safe_notify("Task not complete yet. Please wait for generation to finish.", notify_type="warning")
            return
        download_file(file_url)

    def client_is_active() -> bool:
        try:
            return bool(client.has_socket_connection) and client.id in client.instances
        except Exception:
            return False

    def safe_refresh(target: Any) -> None:
        if not client_is_active():
            return
        try:
            target.refresh()
        except Exception:
            pass

    def attach_tooltip(element: Any, text: str) -> Any:
        try:
            element.tooltip(text)
        except Exception:
            pass
        return element

    def supports_category_expansion(col: Dict[str, Any]) -> bool:
        generator_type = str(col.get("generator_type") or "").strip().lower()
        if generator_type == "categorical":
            return True
        if generator_type == "auto":
            return _infer_generator_from_dtype(col.get("data_type") or "") == "categorical"
        return False

    def sync_expand_checkbox(col: Dict[str, Any], checkbox: Any) -> None:
        enabled = supports_category_expansion(col)
        if not enabled:
            checkbox.set_value(False)
        checkbox.set_enabled(enabled)
        try:
            checkbox.style(replace="opacity: 1;" if enabled else "opacity: 0.55; pointer-events: none;")
        except Exception:
            pass

    def render_page_header(title: str, subtitle: str) -> None:
        with ui.column().classes("gap-1"):
            ui.label(title).classes("text-h4 font-extrabold").style("color: var(--nexus-brand);")
            ui.label(subtitle).classes("text-sm text-slate-600")

    def normalize_data_type_value(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "varchar"
        return TYPE_NORMALIZATION.get(text, text)

    def project_table_count() -> int:
        return len((local_state.get("project_data") or {}).get("tables") or [])

    def project_column_count() -> int:
        return sum(len(table.get("columns") or []) for table in (local_state.get("project_data") or {}).get("tables") or [])

    def current_generation_overview() -> List[str]:
        mode_label = {"csv": "CSV Ingestion", "ddl": "DDL Blueprint", "schema": "Schema Studio"}.get(
            str(local_state.get("setup_mode") or "").strip().lower(),
            "Not selected",
        )
        relation_count = len((local_state.get("project_data") or {}).get("relations") or [])
        generation_table_count = len(local_state.get("generation_table_settings") or {})
        rows_label = (
            "Rows: per table"
            if generation_table_count > 1
            else f"Rows requested: {int(max(1, local_state.get('num_rows') or 1))}"
        )
        return [
            f"Input mode: {mode_label}",
            f"Tables: {project_table_count()}",
            f"Columns: {project_column_count()}",
            f"Relationships: {relation_count}",
            rows_label,
            f"Output: {str(local_state.get('output_format') or 'csv').upper()}",
        ]

    def generation_table_names() -> List[str]:
        tables = (local_state.get("project_data") or {}).get("tables") or []
        return [str(t.get("name") or "") for t in tables if str(t.get("name") or "").strip()]

    def generation_base_rows() -> int:
        settings = local_state.get("generation_table_settings") or {}
        rows = [int(max(1, cfg.get("num_rows") or 1)) for cfg in settings.values() if isinstance(cfg, dict)]
        if rows:
            return max(rows)
        return int(max(1, local_state.get("num_rows") or 100))

    def sync_generation_table_settings() -> None:
        tables = generation_table_names()
        settings = dict(local_state.get("generation_table_settings") or {})
        plan_rows = ((local_state.get("generation_plan") or {}).get("row_counts") or {})
        next_settings: Dict[str, Dict[str, int]] = {}
        default_seed = int(local_state.get("seed") or 42)
        default_rows = int(max(1, local_state.get("num_rows") or 100))
        for table_name in tables:
            current = settings.get(table_name) or {}
            planned_rows = int(max(1, plan_rows.get(table_name) or default_rows))
            next_settings[table_name] = {
                "num_rows": int(max(1, current.get("num_rows") or planned_rows)),
                "seed": int(current.get("seed") if current.get("seed") is not None else default_seed),
            }
        local_state["generation_table_settings"] = next_settings
        selected = str(local_state.get("selected_generation_table") or "")
        if tables and selected not in tables:
            local_state["selected_generation_table"] = tables[0]
        elif not tables:
            local_state["selected_generation_table"] = None
        if tables:
            active = next_settings.get(str(local_state["selected_generation_table"]) or tables[0]) or next_settings[tables[0]]
            local_state["num_rows"] = int(max(1, active.get("num_rows") or default_rows))
            local_state["seed"] = int(active.get("seed") if active.get("seed") is not None else default_seed)

    def generation_settings_for(table_name: Optional[str] = None) -> Dict[str, int]:
        sync_generation_table_settings()
        names = generation_table_names()
        target = str(table_name or local_state.get("selected_generation_table") or (names[0] if names else "")).strip()
        settings = local_state.get("generation_table_settings") or {}
        return dict(settings.get(target) or {"num_rows": int(max(1, local_state.get("num_rows") or 1000)), "seed": int(local_state.get("seed") or 42)})

    def set_selected_generation_table(table_name: str) -> None:
        if table_name not in generation_table_names():
            return
        local_state["selected_generation_table"] = table_name
        active = generation_settings_for(table_name)
        local_state["num_rows"] = int(max(1, active.get("num_rows") or 1))
        local_state["seed"] = int(active.get("seed") if active.get("seed") is not None else 42)
        safe_refresh(generate_view)
        safe_refresh(assistant_widget)

    def set_generation_table_rows(value: Any) -> None:
        table_name = str(local_state.get("selected_generation_table") or "").strip()
        if not table_name:
            return
        try:
            rows = int(float(value or 1))
        except Exception:
            rows = 1
        rows = max(1, rows)
        sync_generation_table_settings()
        local_state["generation_table_settings"][table_name]["num_rows"] = rows
        local_state["num_rows"] = rows

    def set_generation_table_seed(value: Any) -> None:
        table_name = str(local_state.get("selected_generation_table") or "").strip()
        if not table_name:
            return
        try:
            seed = int(float(value or 42))
        except Exception:
            seed = 42
        sync_generation_table_settings()
        local_state["generation_table_settings"][table_name]["seed"] = seed
        local_state["seed"] = seed

    def assistant_current_page() -> str:
        return str(local_state.get("assistant_page") or "upload")

    def assistant_page_title() -> str:
        current_page = assistant_current_page()
        for step in STEPS:
            if step["key"] == current_page:
                return str(step["label"])
        return current_page.title()

    def assistant_set_page(name: str) -> None:
        local_state["assistant_page"] = name
        safe_refresh(assistant_widget)

    def activate_assistant_mode() -> None:
        local_state["assistant_fresh_start"] = False
        if not local_state.get("assistant_mode_active"):
            local_state["assistant_mode_active"] = True
            local_state["assistant_page"] = "upload"

    def assistant_messages() -> List[Dict[str, str]]:
        history = list(local_state.get("assistant_chat") or [])
        if history:
            return history[-12:]

        page = assistant_current_page()
        selected_mode = str(local_state.get("setup_mode") or "").strip().lower()
        mode_titles = {"csv": "CSV Ingestion", "schema": "Schema Studio"}
        current_mode = mode_titles.get(selected_mode)
        if page == "upload":
            text = "Hello, how may I help you? We can get started with CSV Ingestion or Schema Studio."
            if current_mode and not local_state.get("assistant_fresh_start"):
                text = f"Hello, how may I help you? You are on {assistant_page_title()} and {current_mode} is ready to continue."
            return [
                {
                    "role": "assistant",
                    "text": text,
                }
            ]
        if page == "input":
            return [
                {
                    "role": "assistant",
                    "text": "You are on Input. I can answer questions here or move you through the workflow if you tell me what you want to do next.",
                }
            ]
        if page == "project":
            return [
                {
                    "role": "assistant",
                    "text": "You are in Workspace. Ask me about the project structure or tell me to continue to Modeling.",
                }
            ]
        if page == "modeling":
            return [
                {
                    "role": "assistant",
                    "text": "You are in Modeling. I can help explain columns and relationships, or take you to Generate when you are ready.",
                }
            ]
        if page == "generate":
            sample_generated = bool(local_state.get("sample_generated"))
            sample_confirmed = bool(local_state.get("sample_confirmed"))
            
            if sample_confirmed:
                return [
                    {
                        "role": "assistant",
                        "text": "Sample approved! Continue to Output to run full dataset generation.",
                    }
                ]
            elif sample_generated:
                return [
                    {
                        "role": "assistant",
                        "text": "Sample generated and downloaded. Approve it to continue to Output.",
                    }
                ]
            else:
                return [
                    {
                        "role": "assistant",
                        "text": "Generate mode ready. Start by generating a 5-row sample download, then approve it.",
                    }
                ]
        if page == "output":
            return [
                {
                    "role": "assistant",
                    "text": "You are in Output. Review settings, launch full generation, and download the final dataset.",
                }
            ]
        return [
            {
                "role": "assistant",
                "text": "I can help you continue through the workflow.",
            }
        ]

    def ensure_assistant_history() -> None:
        if local_state.get("assistant_chat"):
            return
        local_state["assistant_chat"] = assistant_messages()

    def assistant_next_target() -> Optional[str]:
        page = assistant_current_page()
        if page == "upload" and local_state.get("setup_mode"):
            return "input"
        if page == "input" and local_state.get("project_id"):
            return "project"
        if page == "project" and local_state.get("project_id"):
            return "modeling"
        if page == "modeling" and local_state.get("project_id"):
            return "generate"
        if page == "generate" and bool(local_state.get("sample_confirmed")):
            return "output"
        return None

    def assistant_previous_page() -> Optional[str]:
        page = assistant_current_page()
        if page == "input":
            return "upload"
        if page == "project":
            return "input"
        if page == "modeling":
            return "project"
        if page == "generate":
            return "modeling"
        if page == "output":
            return "generate"
        return None

    def start_new_assistant_chat() -> None:
        local_state["assistant_chat"] = []
        local_state["assistant_input"] = ""
        local_state["assistant_busy"] = False
        local_state["assistant_meta"] = None
        local_state["assistant_mode_active"] = False
        local_state["assistant_page"] = "upload"
        local_state["assistant_fresh_start"] = True
        ensure_assistant_history()
        safe_refresh(assistant_widget)
        scroll_assistant_to_top()

    def scroll_assistant_to_top() -> None:
        try:
            ui.run_javascript(
                """
                [60, 180, 320].forEach((delay) => {
                  setTimeout(() => {
                    const areas = document.querySelectorAll('.assistant-scroll');
                    const area = areas[areas.length - 1];
                    if (area) {
                      area.scrollTop = 0;
                    }
                  }, delay);
                });
                """
            )
        except Exception:
            pass

    def scroll_assistant_to_bottom() -> None:
        try:
            ui.run_javascript(
                """
                [60, 180, 320, 520].forEach((delay) => {
                  setTimeout(() => {
                    const anchors = document.querySelectorAll('.assistant-scroll-anchor');
                    const anchor = anchors[anchors.length - 1];
                    if (anchor) {
                      anchor.scrollIntoView({ behavior: 'auto', block: 'end' });
                      return;
                    }
                    const areas = document.querySelectorAll('.assistant-scroll');
                    const area = areas[areas.length - 1];
                    if (area) {
                      area.scrollTop = area.scrollHeight;
                    }
                  }, delay);
                });
                """
            )
        except Exception:
            pass

    def toggle_assistant_fullscreen() -> None:
        local_state["assistant_fullscreen"] = not bool(local_state.get("assistant_fullscreen"))
        safe_refresh(assistant_widget)

    def approve_sample_preview() -> None:
        local_state["sample_confirmed"] = True
        safe_notify("Sample approved. Continue to Output to generate the full dataset.", notify_type="positive")
        safe_refresh(generate_view)
        safe_refresh(output_view)
        safe_refresh(assistant_widget)

    def should_show_same_table_analytics() -> bool:
        project_data = local_state.get("project_data") or {}
        project = project_data.get("project") or {}
        tables = project_data.get("tables") or []
        # Show same-table analytics for all projects (including single-table CSVs)
        return bool(tables)

    def handle_assistant_local_command(message: str) -> Optional[Dict[str, Any]]:
        text = str(message or "").strip()
        lowered = text.lower()
        page = assistant_current_page()
        has_project = bool(local_state.get("project_id"))
        file_intent = any(
            token in lowered
            for token in [
                "generate using file",
                "generate using files",
                "generate through file",
                "generate through files",
                "upload file",
                "upload files",
                "from file",
                "from files",
            ]
        )

        if file_intent and not has_project:
            select_setup_mode("csv")
            assistant_set_page("input")
            return {
                "reply": "I opened CSV Input. Please upload your file below to start generation from files.",
                "action": {},
                "source": "local",
                "model": None,
            }

        if page == "upload" and "csv" in lowered and any(token in lowered for token in ["upload", "use", "select", "choose"]):
            select_setup_mode("csv")
            assistant_set_page("input")
            return {
                "reply": "I opened the CSV Input step. Please upload your file below.",
                "action": {},
                "source": "local",
                "model": None,
            }
        if page == "upload" and "schema" in lowered and any(token in lowered for token in ["use", "select", "choose", "create"]):
            select_setup_mode("schema")
            assistant_set_page("input")
            return {
                "reply": "I opened Schema Studio. Please define your table and columns below.",
                "action": {},
                "source": "local",
                "model": None,
            }

        requested_columns_match = re.search(r"(\d+)\s+columns?", lowered)
        asks_for_schema_start = any(
            token in lowered
            for token in [
                "generate synthetic data",
                "synthetic data",
                "create synthetic data",
                "create a schema project",
            "schema project",
            "upload csv",
            "use csv",
            "select csv",
            "choose csv",
            "use schema",
            "select schema",
            "choose schema",
        ]
        )
        mentions_file_input = any(token in lowered for token in ["csv", "file", "files", "upload"])
        if not has_project and asks_for_schema_start and not mentions_file_input:
            requested_columns = int(requested_columns_match.group(1)) if requested_columns_match else 1
            select_setup_mode("schema")
            set_schema_column_count(requested_columns)
            assistant_set_page("input")
            if requested_columns_match:
                return {"reply": f"I opened Schema Studio with {requested_columns} blank columns. Please fill in the column details below.", "action": {}, "source": "local", "model": None}
            return {"reply": "I opened Schema Studio so you can define the schema first. Please fill in the column details below.", "action": {}, "source": "local", "model": None}

        if any(token in lowered for token in ["add column", "new column"]):
            if page == "input" and str(local_state.get("setup_mode") or "") == "schema":
                active_index = int(local_state.get("schema_active_table_idx", 0))
                add_schema_column(active_index)
                return {"reply": "Added a new column to the active schema table.", "action": {}, "source": "local", "model": None}

        if any(token in lowered for token in ["add table", "new table"]):
            if page == "input" and str(local_state.get("setup_mode") or "") == "schema":
                add_schema_table()
                return {"reply": "Added a new table to Schema Studio.", "action": {}, "source": "local", "model": None}

        delete_match = re.search(r"(?:delete|remove)\s+(?:file|table|row|column)?\s*(\d+)", lowered)
        if delete_match:
            index = max(1, int(delete_match.group(1)))
            if page == "input" and str(local_state.get("setup_mode") or "") == "schema":
                active_index = int(local_state.get("schema_active_table_idx", 0))
                remove_schema_column(active_index, index - 1)
                return {"reply": f"Removed column {index} from the active schema table.", "action": {}, "source": "local", "model": None}
            if page == "input" and str(local_state.get("setup_mode") or "") == "csv":
                asyncio.create_task(delete_uploaded_table(index))
                return {"reply": f"Removing uploaded file {index}.", "action": {}, "source": "local", "model": None}

        if any(token in lowered for token in ["create schema project", "create workspace", "build workspace"]):
            if page == "input" and str(local_state.get("setup_mode") or "") == "schema":
                asyncio.create_task(create_schema_project())
                return {"reply": "Creating the schema project now.", "action": {}, "source": "local", "model": None}

        rows_match = re.search(r"(\d+)\s*(?:rows?|lines?)", lowered)
        if page in {"generate", "output"} and rows_match:
            local_state["num_rows"] = max(1, int(rows_match.group(1)))
            refresh_all()
            return {"reply": f"Set rows to generate to {local_state['num_rows']}.", "action": {}, "source": "local", "model": None}

        if page in {"generate", "output"} and "parquet" in lowered:
            local_state["output_format"] = "parquet"
            refresh_all()
            return {"reply": "Output format set to parquet.", "action": {}, "source": "local", "model": None}
        if page in {"generate", "output"} and "csv" in lowered and "upload" not in lowered:
            local_state["output_format"] = "csv"
            refresh_all()
            return {"reply": "Output format set to CSV.", "action": {}, "source": "local", "model": None}
        if has_project and any(token in lowered for token in ["skip modeling", "skip model", "generate csv", "generate parquet", "generate data", "start generation", "launch generation"]):
            if rows_match:
                local_state["num_rows"] = max(1, int(rows_match.group(1)))
            if "parquet" in lowered:
                local_state["output_format"] = "parquet"
            elif "csv" in lowered:
                local_state["output_format"] = "csv"
            refresh_all()
            reply_parts = []
            if rows_match:
                reply_parts.append(f"I set the row count to {local_state['num_rows']}.")
            if "parquet" in lowered or "csv" in lowered:
                reply_parts.append(f"Output format is {str(local_state['output_format']).upper()}.")
            reply_parts.append("I am moving you to Generate.")
            return {
                "reply": " ".join(reply_parts),
                "action": {"target_page": "generate"},
                "source": "local",
                "model": None,
            }

        if any(token in lowered for token in ["next", "continue", "proceed", "move ahead", "go next"]):
            target = assistant_next_target()
            if target:
                return {
                    "reply": f"Moving to {target.title()}.",
                    "action": {"target_page": target},
                    "source": "local",
                    "model": None,
                }
        if any(token in lowered for token in ["back", "go back", "previous step", "previous page"]):
            previous = assistant_previous_page()
            if previous:
                return {
                    "reply": f"Going back to {previous.title()}.",
                    "action": {"target_page": previous},
                    "source": "local",
                    "model": None,
                }
        if page == "input" and str(local_state.get("setup_mode") or "") == "csv" and local_state.get("project_id"):
            if any(token in lowered for token in ["no more", "done uploading", "that's all", "thats all", "continue to workspace"]):
                return {
                    "reply": "Perfect. I will keep the current CSV files and move to Workspace.",
                    "action": {"target_page": "project"},
                    "source": "local",
                    "model": None,
                }
            if lowered in {"no", "done", "continue"}:
                return {
                    "reply": "Perfect. I will keep the current CSV files and move to Workspace.",
                    "action": {"target_page": "project"},
                    "source": "local",
                    "model": None,
                }
            if any(token in lowered for token in ["yes", "upload another", "add another", "more files", "another file"]):
                return {
                    "reply": "Upload the next CSV file below whenever you are ready. I will keep asking until you want to continue.",
                    "action": {},
                    "source": "local",
                    "model": None,
                }
        return None

    def should_handle_assistant_locally(message: str) -> bool:
        lowered = str(message or "").strip().lower()
        local_tokens = [
            "generate using file",
            "generate using files",
            "generate through file",
            "generate through files",
            "upload file",
            "upload files",
            "upload csv",
            "use csv",
            "select csv",
            "choose csv",
            "use schema",
            "select schema",
            "choose schema",
            "add column",
            "new column",
            "add table",
            "new table",
            "delete ",
            "remove ",
            "create schema project",
            "create workspace",
            "build workspace",
            " rows",
            " parquet",
            " output format",
            "upload another",
            "another file",
            "synthetic data",
            "schema project",
            "skip modeling",
            "skip model",
            "generate csv",
            "generate parquet",
        ]
        return any(token in lowered for token in local_tokens)

    def local_assistant_fallback(message: str) -> Dict[str, Any]:
        text = str(message or "").strip()
        lowered = text.lower()
        action: Dict[str, Any] = {"setup_mode": None, "target_page": None, "operation": None}
        next_steps = {
            "upload": "Input",
            "input": "Workspace",
            "project": "Modeling",
            "modeling": "Generate",
            "generate": "Output" if bool(local_state.get("sample_confirmed")) else "Generate",
            "output": "Output",
        }

        if lowered in {"hi", "hello", "hey", "hii", "hola"}:
            return {
                "reply": f"Hello, how may I help you? You are currently on {assistant_page_title()}.",
                "action": action,
                "source": "local",
                "model": None,
            }

        if any(
            token in lowered
            for token in [
                "generate using file",
                "generate using files",
                "generate through file",
                "generate through files",
                "upload file",
                "upload files",
                "from file",
                "from files",
            ]
        ):
            action["setup_mode"] = "csv"
            action["target_page"] = "input"
            return {
                "reply": "Sure, I switched to CSV flow. Please upload your file below on Input.",
                "action": action,
                "source": "local",
                "model": None,
            }

        if any(token in lowered for token in ["where should i upload", "where do i upload", "where can i upload", "how do i upload", "upload where"]):
            if assistant_current_page() == "upload" and str(local_state.get("setup_mode") or "") == "csv":
                action["target_page"] = "input"
                return {
                    "reply": "The CSV uploader is on the Input step. I am taking you there now.",
                    "action": action,
                    "source": "local",
                    "model": None,
                }

        if "csv" in lowered:
            action["setup_mode"] = "csv"
            if assistant_current_page() == "upload":
                action["target_page"] = "input"
            return {
                "reply": "CSV mode selected. I am moving you to Input so you can continue with the CSV flow.",
                "action": action,
                "source": "local",
                "model": None,
            }
        if "schema" in lowered:
            action["setup_mode"] = "schema"
            if assistant_current_page() == "upload":
                action["target_page"] = "input"
            return {
                "reply": "Schema Studio mode selected. I am moving you to Input so you can start defining tables.",
                "action": action,
                "source": "local",
                "model": None,
            }
        if "workspace" in lowered:
            action["target_page"] = "project"
            return {"reply": "Opening Workspace.", "action": action, "source": "local", "model": None}
        if "modeling" in lowered:
            action["target_page"] = "modeling"
            return {"reply": "Taking you to Modeling.", "action": action, "source": "local", "model": None}
        if "generate" in lowered:
            if local_state.get("project_id"):
                action["target_page"] = "generate"
                return {"reply": "Taking you to Generate.", "action": action, "source": "local", "model": None}
            return {
                "reply": f"To stay on workflow, the next step is {next_steps.get(assistant_current_page(), 'Input')}.",
                "action": {},
                "source": "local",
                "model": None,
            }
        if "setup" in lowered:
            action["target_page"] = "upload"
            return {"reply": "Taking you back to Setup.", "action": action, "source": "local", "model": None}
        if "input" in lowered:
            action["target_page"] = "input"
            return {
                "reply": "Opening Input.",
                "action": action,
                "source": "local",
                "model": None,
            }

        return {
            "reply": f"I can keep you on the workflow here. The next step is {next_steps.get(assistant_current_page(), 'Input')}.",
            "action": action,
            "source": "local",
            "model": None,
        }

    def assistant_choices() -> List[Dict[str, Any]]:
        page = assistant_current_page()
        selected_mode = str(local_state.get("setup_mode") or "").strip().lower()
        fresh_start = bool(local_state.get("assistant_fresh_start"))
        actions: List[Dict[str, Any]] = []
        if page == "upload":
            actions.append(
                {
                    "label": "CSV Ingestion (Selected)" if selected_mode == "csv" and not fresh_start else "CSV Ingestion",
                    "action": {"setup_mode": "csv", "target_page": "input"},
                }
            )
            actions.append(
                {
                    "label": "Schema Studio (Selected)" if selected_mode == "schema" and not fresh_start else "Schema Studio",
                    "action": {"setup_mode": "schema", "target_page": "input"},
                }
            )
        elif page == "input":
            actions.append({"label": "Go Back", "action": {"target_page": "upload"}})
            if selected_mode == "csv":
                if bool(local_state.get("project_id")) and local_state.get("multi_csv_inflight", 0) == 0:
                    actions.append({"label": "Upload Another File", "action": {}})
                    actions.append({"label": "Continue", "action": {"target_page": "project"}})
            elif selected_mode == "schema":
                if bool(local_state.get("project_id")):
                    actions.append({"label": "Continue", "action": {"target_page": "project"}})
        elif page == "project":
            actions.append({"label": "Go Back", "action": {"target_page": "input"}})
            if bool(local_state.get("project_id")) and not local_state.get("is_loading_summary"):
                actions.append({"label": "Refresh Summary", "action": {"operation": "refresh_summary"}})
            if bool(local_state.get("project_id")):
                actions.append({"label": "Go to Modeling", "action": {"target_page": "modeling"}})
        elif page == "modeling":
            actions.append({"label": "Go Back", "action": {"target_page": "project"}})
            if bool(local_state.get("project_id")) and not local_state.get("is_inferring_semantics"):
                actions.append({"label": "Infer Semantics", "action": {"operation": "infer_semantics"}})
            if bool(local_state.get("project_id")):
                actions.append({"label": "Go to Generate", "action": {"target_page": "generate"}})
        elif page == "generate":
            actions.append({"label": "Go Back", "action": {"target_page": "modeling"}})
            if bool(local_state.get("project_id")):
                sample_generated = bool(local_state.get("sample_generated"))
                sample_ready = bool(local_state.get("sample_confirmed"))
                if not sample_generated:
                    actions.append({"label": "Generate Sample", "action": {"operation": "launch_generation_sample"}})
                elif not sample_ready:
                    actions.append({"label": "Approve Sample", "action": {"operation": "approve_sample"}})
                if sample_ready:
                    actions.append({"label": "Continue to Output", "action": {"target_page": "output"}})
        else:
            actions.append({"label": "Go Back", "action": {"target_page": "generate"}})
            sample_ready = bool(local_state.get("sample_confirmed"))
            if sample_ready and local_state.get("task_status") != "running":
                actions.append({"label": "Generate Full Dataset", "action": {"operation": "launch_generation"}})
            if local_state.get("task_status") == "done" and local_state.get("task_file_url"):
                actions.append({"label": "Download Output", "download": True})
        return actions

    def toggle_assistant() -> None:
        local_state["assistant_open"] = not bool(local_state.get("assistant_open"))
        if local_state.get("assistant_open"):
            start_new_assistant_chat()
        safe_refresh(assistant_widget)

    def push_assistant_message(role: str, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        history = list(local_state.get("assistant_chat") or [])
        history.append({"role": role, "text": text})
        local_state["assistant_chat"] = history[-14:]
        safe_refresh(assistant_widget)
        scroll_assistant_to_bottom()

    def push_assistant_message_if_new(role: str, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        history = list(local_state.get("assistant_chat") or [])
        if history and str(history[-1].get("role")) == role and str(history[-1].get("text") or "").strip() == text:
            return
        push_assistant_message(role, text)

    def assistant_ack_for_choice(choice: Dict[str, Any]) -> str:
        action = (choice or {}).get("action") or {}
        target_page = str(action.get("target_page") or "").strip().lower()
        setup_mode = str(action.get("setup_mode") or local_state.get("setup_mode") or "").strip().lower()
        operation = str(action.get("operation") or "").strip().lower()

        if operation == "refresh_summary":
            return "Refreshing the workspace summary."
        if operation == "refresh_plan":
            return "Refreshing the dependency plan."
        if operation == "infer_semantics":
            return "Running semantic inference on your columns."
        if operation == "launch_generation_sample":
            return "Generating a 5-row sample preview for you to review."
        if operation == "approve_sample":
            return "Approving the sample. You can now generate the full dataset."
        if operation == "launch_generation":
            return "Starting the full generation run."

        if target_page == "upload" and not setup_mode:
            return "Back to Setup. Choose the input path you want to use."
        if target_page == "input" and setup_mode == "csv":
            return "CSV selected. Upload your file below to create the workspace."
        if target_page == "input" and setup_mode == "schema":
            return "Schema Studio selected. Define tables and columns below, then create the workspace."
        if not target_page and not operation and setup_mode == "csv":
            return "CSV mode is ready. Upload your next file below whenever you are ready."
        if target_page == "project":
            return "Workspace opened. Review tables and relationships below."
        if target_page == "modeling":
            return "Modeling opened. Edit columns and relationships below."
        if target_page == "generate":
            return "Generate opened. First generate a 5-row sample download, then approve it."
        if target_page == "output":
            return "Output opened. Review settings and launch full generation when ready."
        return ""

    def normalize_assistant_action(action: Optional[Dict[str, Any]], message: str = "") -> Dict[str, Any]:
        normalized = dict(action or {})
        setup_mode = str(normalized.get("setup_mode") or "").strip().lower()
        target_page = str(normalized.get("target_page") or "").strip().lower()
        lowered = str(message or "").strip().lower()

        if assistant_current_page() == "upload":
            if "csv" in lowered and any(token in lowered for token in ["upload", "use", "select", "choose"]):
                normalized["setup_mode"] = "csv"
                normalized["target_page"] = "input"
                return normalized
            if "schema" in lowered and any(token in lowered for token in ["use", "select", "choose", "create"]):
                normalized["setup_mode"] = "schema"
                normalized["target_page"] = "input"
                return normalized
            if setup_mode in {"csv", "schema"} and not target_page:
                normalized["target_page"] = "input"
            elif not setup_mode and not target_page:
                if "csv" in lowered and any(token in lowered for token in ["upload", "use", "select", "choose"]):
                    normalized["setup_mode"] = "csv"
                    normalized["target_page"] = "input"
                elif "schema" in lowered and any(token in lowered for token in ["use", "select", "choose", "create"]):
                    normalized["setup_mode"] = "schema"
                    normalized["target_page"] = "input"
            if str(local_state.get("setup_mode") or "") == "csv" and any(token in lowered for token in ["where should i upload", "where do i upload", "where can i upload", "how do i upload", "upload where"]):
                normalized["target_page"] = "input"
        return normalized

    def apply_assistant_action(action: Optional[Dict[str, Any]]) -> None:
        action = action or {}
        setup_mode = str(action.get("setup_mode") or "").strip().lower()
        target_page = str(action.get("target_page") or "").strip().lower()
        operation = str(action.get("operation") or "").strip().lower()

        if setup_mode in {"csv", "schema"}:
            select_setup_mode(setup_mode)
        if target_page in {"upload", "input", "project", "modeling", "generate", "output"}:
            if local_state.get("assistant_mode_active"):
                asyncio.create_task(assistant_navigate_with_save(target_page))
            else:
                go_to_page(target_page)
        if operation == "refresh_summary":
            asyncio.create_task(refresh_project_summary(show_notify=True))
        elif operation == "refresh_plan":
            asyncio.create_task(refresh_generation_plan())
        elif operation == "infer_semantics":
            asyncio.create_task(infer_semantics_with_ai())
        elif operation == "launch_generation_sample":
            asyncio.create_task(start_generation(sample_only=True))
        elif operation == "approve_sample":
            approve_sample_preview()
        elif operation == "launch_generation":
            asyncio.create_task(start_generation())

    def handle_assistant_choice(choice: Dict[str, Any]) -> None:
        activate_assistant_mode()
        # Store option-clicks as user messages so the conversation reads naturally.
        push_assistant_message("user", str(choice.get("label") or "Selected option"))
        if choice.get("download") and local_state.get("task_file_url"):
            file_url = local_state.get("task_file_url")
            if file_url:
                download_file(file_url)
                push_assistant_message("assistant", "Downloading your generated dataset. Check your downloads folder.")
            else:
                push_assistant_message("assistant", "Download URL is not available. Please try generating again.")
            return
        apply_assistant_action(choice.get("action"))
        ack = assistant_ack_for_choice(choice)
        if ack:
            push_assistant_message_if_new("assistant", ack)
        safe_refresh(assistant_widget)
        scroll_assistant_to_bottom()

    ACTION_BAR = "w-full justify-end items-center gap-3 mt-2 flex-wrap"
    BUTTON_BASE = "font-bold rounded-lg shadow-sm"
    BUTTON_PADDING = "px-4 py-2"
    BUTTON_PADDING_COMPACT = "px-3 py-1 text-xs"
    BUTTON_VARIANTS: Dict[str, str] = {
        "primary": "background-color: var(--nexus-brand); color: #ffffff;",
        "outline": "color: var(--nexus-brand); border-color: var(--nexus-brand);",
        "success": "background-color: #059669; color: #ffffff;",
        "warning": "background-color: #d97706; color: #ffffff;",
        "danger": "background-color: #e11d48; color: #ffffff;",

    }

    def action_button(
        label: str,
        *,
        icon: Optional[str] = None,
        on_click: Optional[Any] = None,
        variant: str = "primary",
        compact: bool = False,
    ) -> Any:
        button = ui.button(label, icon=icon, on_click=on_click).props("no-caps")
        if variant == "outline":
            button.props("outline")
        button.classes(BUTTON_BASE)
        button.classes(BUTTON_PADDING_COMPACT if compact else BUTTON_PADDING)
        button.style(BUTTON_VARIANTS.get(variant, BUTTON_VARIANTS["primary"]))
        return button

    def uploaded_success_count() -> int:
        return sum(1 for row in local_state["uploaded_tables"] if row.get("status") == "Uploaded")

    def add_upload_row(file_name: str, mode: str) -> int:
        row = {
            "no": len(local_state["uploaded_tables"]) + 1,
            "file_name": file_name,
            "mode": mode,
            "status": "Uploading",
            "message": "In progress",
            "table_id": None,
            "project_id": None,
        }
        local_state["uploaded_tables"].append(row)
        return len(local_state["uploaded_tables"]) - 1

    def update_upload_row(
        row_index: int,
        status: str,
        message: str,
        *,
        table_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        if 0 <= row_index < len(local_state["uploaded_tables"]):
            local_state["uploaded_tables"][row_index]["status"] = status
            local_state["uploaded_tables"][row_index]["message"] = message
            if table_id is not None:
                local_state["uploaded_tables"][row_index]["table_id"] = table_id
            if project_id is not None:
                local_state["uploaded_tables"][row_index]["project_id"] = project_id

    def normalize_upload_rows() -> None:
        for idx, row in enumerate(local_state["uploaded_tables"]):
            row["no"] = idx + 1

    def new_schema_column() -> Dict[str, Any]:
        return {
            "name": "",
            "data_type": "varchar",
            "description": "",
            "mandatory": True,
            "is_unique": False,
            "generator_type": "auto",
            "allowed_values": "",
            "expand_categories": False,
        }

    def new_schema_table() -> Dict[str, Any]:
        return {
            "table_name": "",
            "description": "",
            "columns": [new_schema_column()],
        }

    def reset_schema_builder() -> None:
        local_state["schema_project_name"] = ""
        local_state["schema_tables"] = [new_schema_table()]
        local_state["schema_active_table_idx"] = 0

    def set_schema_column_count(column_count: int) -> None:
        target_count = max(1, int(column_count))
        if not local_state["schema_tables"]:
            local_state["schema_tables"] = [new_schema_table()]
        local_state["schema_active_table_idx"] = 0
        local_state["schema_tables"][0]["columns"] = [new_schema_column() for _ in range(target_count)]
        refresh_all()

    def set_active_schema_table(table_index: int) -> None:
        if 0 <= table_index < len(local_state["schema_tables"]):
            local_state["schema_active_table_idx"] = table_index
            refresh_all()

    def add_schema_table() -> None:
        local_state["schema_tables"].append(new_schema_table())
        local_state["schema_active_table_idx"] = len(local_state["schema_tables"]) - 1
        refresh_all()

    def remove_schema_table(table_index: int) -> None:
        if len(local_state["schema_tables"]) <= 1:
            safe_notify("At least one table is required.", notify_type="warning")
            return
        if 0 <= table_index < len(local_state["schema_tables"]):
            local_state["schema_tables"].pop(table_index)
            if local_state["schema_active_table_idx"] >= len(local_state["schema_tables"]):
                local_state["schema_active_table_idx"] = len(local_state["schema_tables"]) - 1
            elif local_state["schema_active_table_idx"] > table_index:
                local_state["schema_active_table_idx"] -= 1
            refresh_all()

    def add_schema_column(table_index: int) -> None:
        if 0 <= table_index < len(local_state["schema_tables"]):
            local_state["schema_tables"][table_index]["columns"].append(new_schema_column())
            refresh_all()

    def remove_schema_column(table_index: int, col_index: int) -> None:
        if not (0 <= table_index < len(local_state["schema_tables"])):
            return
        cols = local_state["schema_tables"][table_index]["columns"]
        if len(cols) <= 1:
            safe_notify("Each table must have at least one column.", notify_type="warning")
            return
        if 0 <= col_index < len(cols):
            cols.pop(col_index)
            refresh_all()

    def select_setup_mode(mode: str) -> None:
        local_state["setup_mode"] = str(mode or "").strip().lower()
        refresh_all()

    def clear_active_project_state(*, clear_uploaded: bool = False) -> None:
        local_state["project_id"] = None
        app.storage.user["project_id"] = None
        local_state["project_data"] = None
        local_state["selected_table"] = None
        local_state["selected_generation_table"] = None
        local_state["generation_table_settings"] = {}
        local_state["generation_plan"] = None
        local_state["project_summary"] = None
        local_state["task_id"] = None
        local_state["task_status"] = "idle"
        local_state["task_progress"] = 0
        local_state["task_logs"] = []
        local_state["task_file_url"] = None
        local_state["sample_generated"] = False
        local_state["sample_confirmed"] = False
        local_state["sample_preview_tables"] = []
        local_state["sample_preview_error"] = None
        local_state["editable_relations"] = []
        if clear_uploaded:
            local_state["uploaded_tables"] = []

    reset_schema_builder()

    async def parse_response(resp: httpx.Response) -> Any:
        if resp.is_success:
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                return resp.json()
            return None
        if resp.status_code == 401:
            clear_login_session(notify=True)
            safe_refresh(login_view)
        detail: Any = resp.text
        try:
            payload = resp.json()
            detail = payload.get("detail", payload)
        except Exception:
            pass
        raise APIError(resp.status_code, detail)

    async def load_project(refresh_plan: bool = True, refresh_summary: bool = False) -> None:
        if not local_state["project_id"] or local_state["is_loading_project"]:
            return
        local_state["is_loading_project"] = True
        refresh_all()
        try:
            async with api_client(timeout=30.0) as client:
                project_resp = await client.get(f"{BACKEND_URL}/project/{local_state['project_id']}")
                local_state["project_data"] = await parse_response(project_resp)
                dedupe_project_tables()
                if refresh_plan:
                    rows = generation_base_rows()
                    plan_resp = await client.get(
                        f"{BACKEND_URL}/project/{local_state['project_id']}/plan",
                        params={"base_rows": rows},
                        timeout=20.0,
                    )
                    local_state["generation_plan"] = await parse_response(plan_resp)
                if refresh_summary:
                    local_state["is_loading_summary"] = True
                    summary_resp = await client.get(
                        f"{BACKEND_URL}/project/{local_state['project_id']}/summary",
                        timeout=30.0,
                    )
                    local_state["project_summary"] = await parse_response(summary_resp)
                    local_state["is_loading_summary"] = False

            tables = local_state["project_data"].get("tables", [])
            source_type = str(local_state["project_data"].get("project", {}).get("source_type", "")).upper()
            if source_type == "SCHEMA":
                for table in tables:
                    for col in table.get("columns", []):
                        gen = str(col.get("generator_type") or "auto").strip().lower()
                        if gen == "auto":
                            col["generator_type"] = _infer_generator_from_dtype(col.get("data_type") or "")
            names = [t["name"] for t in tables]
            if names and local_state["selected_table"] not in names:
                local_state["selected_table"] = names[0]
            normalize_project_columns()
            sync_editable_relations_from_project()
            sync_generation_table_settings()
        except Exception as ex:
            safe_notify(f"Project sync failed: {ex}", notify_type="negative")
        finally:
            local_state["is_loading_project"] = False
            local_state["is_loading_summary"] = False
            refresh_all()

    async def refresh_generation_plan() -> None:
        if not local_state["project_id"]:
            return
        try:
            rows = generation_base_rows()
            async with api_client(timeout=20.0) as client:
                plan_resp = await client.get(
                    f"{BACKEND_URL}/project/{local_state['project_id']}/plan",
                    params={"base_rows": rows},
                )
                local_state["generation_plan"] = await parse_response(plan_resp)
            sync_generation_table_settings()
            safe_refresh(generate_view)
        except Exception as ex:
            safe_notify(f"Plan refresh failed: {ex}", notify_type="negative")

    async def fetch_sample_preview(task_id: Optional[str]) -> None:
        if not task_id:
            return
        try:
            async with api_client(timeout=30.0) as client:
                resp = await client.get(f"{BACKEND_URL}/task/{task_id}/preview", params={"rows": 5})
                data = await parse_response(resp)
            local_state["sample_preview_tables"] = list(data.get("tables") or [])
            local_state["sample_preview_error"] = None
        except Exception as ex:
            local_state["sample_preview_tables"] = []
            local_state["sample_preview_error"] = str(ex)
        finally:
            safe_refresh(generate_view)
            safe_refresh(assistant_widget)

    async def refresh_project_summary(show_notify: bool = False) -> None:
        if not local_state["project_id"]:
            return
        local_state["is_loading_summary"] = True
        safe_refresh(project_view)
        try:
            async with api_client(timeout=30.0) as client:
                summary_resp = await client.get(f"{BACKEND_URL}/project/{local_state['project_id']}/summary")
                local_state["project_summary"] = await parse_response(summary_resp)
            if show_notify:
                source = local_state["project_summary"].get("source", "unknown")
                safe_notify(f"Summary refreshed via {source}.", notify_type="positive")
        except Exception as ex:
            safe_notify(f"Summary refresh failed: {ex}", notify_type="negative")
        finally:
            local_state["is_loading_summary"] = False
            safe_refresh(project_view)

    async def send_assistant_message() -> None:
        message = str(local_state.get("assistant_input") or "").strip()
        if not message or local_state.get("assistant_busy"):
            return

        activate_assistant_mode()
        push_assistant_message("user", message)
        local_state["assistant_input"] = ""
        local_state["assistant_busy"] = True
        local_state["assistant_meta"] = None
        safe_refresh(assistant_widget)

        try:
            data = None
            if should_handle_assistant_locally(message):
                data = handle_assistant_local_command(message)
            if data is None:
                payload = {
                    "page": assistant_current_page(),
                    "setup_mode": local_state.get("setup_mode"),
                    "project_id": local_state.get("project_id"),
                    "message": message,
                    "history": list(local_state.get("assistant_chat") or [])[-8:],
                }
                async with api_client(timeout=60.0) as client:
                    resp = await client.post(f"{BACKEND_URL}/assistant/chat", json=payload)
                    data = await parse_response(resp)
            if data is None:
                data = handle_assistant_local_command(message)
        except Exception as ex:
            data = local_assistant_fallback(message)
            if "404" not in str(ex):
                data["reply"] = f"{data.get('reply')} The live chat service is unavailable right now, so I handled this locally."
        finally:
            reply = str(data.get("reply") or "").strip() or "I couldn't form a reply for that yet."
            push_assistant_message("assistant", reply)
            local_state["assistant_meta"] = None
            apply_assistant_action(normalize_assistant_action(data.get("action"), message))
            local_state["assistant_busy"] = False
            refresh_all()
            scroll_assistant_to_bottom()

    def render_assistant_stage_panel() -> None:
        page = assistant_current_page()
        setup_mode = str(local_state.get("setup_mode") or "").strip().lower()
        if page == "upload":
            return
        if page == "input" and setup_mode == "csv":
            with ui.card().classes("w-full bg-slate-50 border border-slate-200 rounded-xl p-4 shadow-none"):
                ui.label("Chat Flow: CSV Upload").classes("text-sm font-bold text-slate-700")
                ui.label("Use this uploader to keep building the CSV workflow inside the assistant.").classes("text-xs text-slate-500 mb-2")
                ui.upload(on_upload=handle_csv_upload, label="Drop CSV file", auto_upload=True).props(
                    "accept=.csv,text/csv"
                ).classes("w-full upload-zone")
                ui.label(
                    "You can keep adding CSV files here. When you are done, continue to Workspace so you can review the project before Modeling."
                ).classes("text-xs text-slate-500 mt-2")
                if local_state["multi_csv_inflight"] > 0:
                    with ui.row().classes("w-full justify-start items-center gap-2 mt-2 text-sky-700"):
                        ui.spinner(size="sm")
                        ui.label(f"Processing {local_state['multi_csv_inflight']} file(s)...")
                if local_state["uploaded_tables"]:
                    with ui.card().classes("upload-file-shell p-2 mt-3"):
                        ui.label("Uploaded Files").classes("text-sm font-bold text-slate-700 mb-1")
                        with ui.column().classes("w-full gap-2"):
                            with ui.row().classes("w-full text-xs text-slate-500 font-bold px-2 upload-table-head"):
                                ui.label("#").classes("text-center")
                                ui.label("File")
                                ui.label("Type")
                                ui.label("Status")
                                ui.label("Action").classes("text-center")
                            for row in local_state["uploaded_tables"]:
                                with ui.row().classes(
                                    "w-full bg-white/60 rounded border border-slate-100 px-2 py-1 upload-table-row"
                                ):
                                    ui.label(str(row["no"])).classes("text-xs text-slate-600 text-center")
                                    ui.label(str(row["file_name"])).classes("text-xs text-slate-700 cell-truncate")
                                    ui.label(str(row["mode"])).classes("text-xs text-slate-600")
                                    ui.label(str(row["status"])).classes("text-xs text-slate-700")
                                    delete_btn = ui.button(icon="delete").props("flat dense round color=negative size=sm")
                                    delete_btn.classes("justify-self-center")
                                    delete_btn.on_click(lambda _, row_no=row["no"]: asyncio.create_task(delete_uploaded_table(row_no)))
                                    delete_btn.set_enabled(str(row.get("status", "")) == "Uploaded")
        elif page == "input" and setup_mode == "schema":
            with ui.card().classes("w-full bg-slate-50 border border-slate-200 rounded-xl p-4 shadow-none"):
                ui.label("Chat Flow: Schema Studio").classes("text-sm font-bold text-slate-700")
                ui.label("Build schema manually here and create the workspace when ready.").classes("text-xs text-slate-500 mb-3")
                ui.input("Project name (optional)").bind_value(local_state, "schema_project_name").classes("w-full mb-3")

                with ui.row().classes("w-full items-center justify-between gap-2 mb-2"):
                    ui.label("Tables").classes("text-xs font-bold uppercase tracking-wide text-slate-500")
                    action_button("Add Table", icon="add", on_click=add_schema_table, variant="outline", compact=True)

                with ui.column().classes("w-full gap-2"):
                    for t_idx, table in enumerate(local_state["schema_tables"]):
                        is_active = int(local_state.get("schema_active_table_idx", 0)) == t_idx
                        with ui.card().classes(f"w-full bg-white/80 border rounded-lg p-3 shadow-none {'border-sky-300' if is_active else 'border-slate-200'}"):
                            with ui.row().classes("w-full items-center justify-between gap-2"):
                                ui.label(str(table.get("table_name") or f"Table {t_idx + 1}")).classes("text-sm font-bold text-slate-700")
                                with ui.row().classes("gap-1"):
                                    edit_btn = ui.button("Editing" if is_active else "Edit", on_click=lambda _, i=t_idx: set_active_schema_table(i)).props("flat dense no-caps")
                                    if is_active:
                                        edit_btn.set_enabled(False)
                                    del_btn = ui.button(icon="delete", on_click=lambda _, i=t_idx: remove_schema_table(i)).props("flat dense round color=negative size=sm")
                                    del_btn.set_enabled(len(local_state["schema_tables"]) > 1)
                            ui.label(f"Columns: {len(table.get('columns', []))}").classes("text-xs text-slate-500")

                active_index = int(local_state.get("schema_active_table_idx", 0))
                active_table: Optional[Dict[str, Any]] = None
                if 0 <= active_index < len(local_state["schema_tables"]):
                    active_table = local_state["schema_tables"][active_index]

                if active_table:
                    with ui.card().classes("w-full bg-white/85 border border-slate-200 rounded-lg p-3 shadow-none mt-3"):
                        ui.label("Table Editor").classes("text-xs font-bold uppercase tracking-wide text-slate-500 mb-2")
                        ui.input("Table name *").bind_value(active_table, "table_name").classes("w-full mb-2")
                        ui.input("Description").bind_value(active_table, "description").classes("w-full mb-3")
                        ui.label("Columns").classes("text-xs font-bold uppercase tracking-wide text-slate-500 mb-2")
                        for c_idx, col in enumerate(active_table.get("columns", [])):
                            with ui.card().classes("w-full bg-slate-50 border border-slate-200 rounded-lg p-3 shadow-none mb-2"):
                                ui.input("Column name *").bind_value(col, "name").classes("w-full mb-2")
                                with ui.row().classes("w-full gap-2 flex-wrap"):
                                    ui.select(SCHEMA_TYPE_OPTIONS, label="Type").bind_value(col, "data_type").classes("w-40")
                                    ui.select(
                                        ["auto", "categorical", "integer", "numerical", "datetime"],
                                        label="Generator",
                                    ).bind_value(col, "generator_type").classes("w-44")
                                ui.input("Allowed Values", placeholder="e.g. Sales, HR, Finance or one per line").bind_value(col, "allowed_values").classes("w-full mt-2")
                                ui.input("Description").bind_value(col, "description").classes("w-full mt-2")
                                with ui.row().classes("w-full items-center justify-between gap-2 mt-2 flex-wrap"):
                                    with ui.row().classes("gap-3 flex-wrap"):
                                        ui.checkbox("Mandatory").bind_value(col, "mandatory")
                                        ui.checkbox("Unique").bind_value(col, "is_unique")
                                        ui.checkbox("Expand Categories").bind_value(col, "expand_categories")
                                    rm_col_btn = ui.button(icon="delete", on_click=lambda _, ti=active_index, ci=c_idx: remove_schema_column(ti, ci)).props("flat dense round color=negative size=sm")
                                    rm_col_btn.set_enabled(len(active_table.get("columns", [])) > 1)
                        with ui.row().classes("w-full justify-between items-center gap-2 mt-2 flex-wrap"):
                            action_button("Add Column", icon="add", on_click=lambda ti=active_index: add_schema_column(ti), variant="outline", compact=True)
                            create_btn = action_button(
                                "Create Schema Project",
                                icon="schema",
                                on_click=lambda: asyncio.create_task(create_schema_project()),
                                variant="success",
                                compact=True,
                            )
                            create_btn.set_enabled(not local_state.get("is_submitting_schema"))
        elif page == "project":
            with ui.card().classes("w-full bg-slate-50 border border-slate-200 rounded-xl p-4 shadow-none"):
                ui.label("Chat Flow: Workspace").classes("text-sm font-bold text-slate-700")
                if local_state.get("is_loading_summary"):
                    ui.label("Refreshing summary...").classes("text-xs text-slate-500")
                elif (local_state.get("project_summary") or {}).get("summary"):
                    ui.label(local_state["project_summary"]["summary"]).classes("text-sm text-slate-700")
                elif local_state.get("project_data"):
                    table_count = len(local_state["project_data"].get("tables", []))
                    ui.label(f"Project loaded with {table_count} table(s).").classes("text-sm text-slate-600")
                else:
                    ui.label("Workspace is not ready yet.").classes("text-sm text-slate-500")
                if local_state.get("project_data"):
                    project = local_state["project_data"]["project"]
                    tables = local_state["project_data"]["tables"]
                    relations = local_state["project_data"]["relations"]
                    with ui.row().classes("w-full gap-2 flex-wrap mt-3"):
                        with ui.card().classes("bg-white/80 border border-slate-200 rounded-lg p-3 shadow-none min-w-[8rem]"):
                            ui.label("Tables").classes("text-xs text-slate-500")
                            ui.label(str(len(tables))).classes("text-lg font-extrabold text-slate-700")
                        with ui.card().classes("bg-white/80 border border-slate-200 rounded-lg p-3 shadow-none min-w-[8rem]"):
                            ui.label("Relations").classes("text-xs text-slate-500")
                            ui.label(str(len(relations))).classes("text-lg font-extrabold text-slate-700")
                        with ui.card().classes("bg-white/80 border border-slate-200 rounded-lg p-3 shadow-none min-w-[8rem]"):
                            ui.label("Source").classes("text-xs text-slate-500")
                            ui.label(str(project.get("source_type", "UNKNOWN"))).classes("text-sm font-bold text-slate-700")

                    names = [t["name"] for t in tables]
                    if names and local_state["selected_table"] not in names:
                        local_state["selected_table"] = names[0]
                    ui.select(
                        names,
                        value=local_state["selected_table"],
                        label="Active table",
                        on_change=lambda e: (
                            local_state.__setitem__("selected_table", e.value),
                            safe_refresh(assistant_widget),
                        ),
                    ).classes("w-full mt-3")
                    selected = current_table()
                    if selected:
                        ui.aggrid(
                            {
                                "columnDefs": [
                                    {"headerName": "Column", "field": "name", "sortable": True, "filter": True, "minWidth": 140},
                                    {"headerName": "Type", "field": "data_type", "width": 120},
                                ],
                                "rowData": selected["columns"],
                                "defaultColDef": {"resizable": True},
                            }
                        ).classes("h-52 w-full mt-3")

                    if relations:
                        mermaid = "graph TD\n"
                        for rel in relations:
                            mermaid += f"    {rel['to_table']} --> {rel['from_table']}\n"
                        ui.label("Relation Graph").classes("text-xs font-bold uppercase tracking-wide text-slate-500 mt-3")
                        ui.mermaid(mermaid).classes("w-full h-56")
        elif page == "modeling":
            with ui.card().classes("w-full bg-slate-50 border border-slate-200 rounded-xl p-4 shadow-none"):
                ui.label("Chat Flow: Modeling").classes("text-sm font-bold text-slate-700")
                if not local_state.get("project_data"):
                    ui.label("Load a workspace first to edit modeling settings.").classes("text-sm text-slate-500")
                else:
                    with ui.row().classes("w-full items-center justify-between gap-2 mb-2 flex-wrap"):
                        with ui.row().classes("items-center gap-2 flex-wrap"):
                            pii_btn = action_button(
                                "Auto Detect PII",
                                icon="privacy_tip",
                                on_click=detect_pii_with_ai,
                                variant="danger",
                                compact=True,
                            )
                            pii_btn.set_enabled(not local_state["is_detecting_pii"])
                            infer_btn = action_button(
                                "Auto Infer (AI)",
                                icon="auto_awesome",
                                on_click=infer_semantics_with_ai,
                                variant="warning",
                                compact=True,
                            )
                            infer_btn.set_enabled(not local_state["is_inferring_semantics"])
                            action_button("Save Blueprint", icon="save", on_click=save_modeling, variant="success", compact=True)
                            action_button(
                                "Go to Generate",
                                icon="arrow_forward",
                                on_click=lambda: assistant_set_page("generate"),
                                variant="primary",
                                compact=True,
                            )
                    with ui.scroll_area().classes("h-80 w-full pr-2"):
                        with ui.column().classes("w-full gap-3"):
                            for table in local_state["project_data"].get("tables", []):
                                render_modeling_table_editor(table, compact=True)
                    ui.label("Relationship Studio").classes("text-xs font-bold uppercase tracking-wide text-slate-500 mt-3")
                    tables = table_names_in_project()
                    if len(tables) <= 1:
                        ui.label("Add at least two tables to define relationships.").classes("text-sm text-slate-500 italic")
                    else:
                        with ui.row().classes("w-full items-center justify-between gap-2 mb-2 flex-wrap"):
                            with ui.row().classes("items-center gap-2"):
                                infer_rel_btn = action_button(
                                    "Infer Relationships",
                                    icon="auto_awesome",
                                    on_click=infer_relationships_with_ai,
                                    variant="warning",
                                    compact=True,
                                )
                                infer_rel_btn.set_enabled(not local_state["is_inferring_relations"])
                                action_button("Add Relationship", icon="add_link", on_click=add_relation_row, variant="outline", compact=True)
                            save_rel_btn = action_button("Save Relationships", icon="save", on_click=save_relationships, variant="success", compact=True)
                            save_rel_btn.set_enabled(not local_state["is_saving_relations"])
                        for r_idx, rel in enumerate(local_state.get("editable_relations", [])):
                            with ui.card().classes("w-full bg-white/80 border border-slate-200 rounded-lg p-3 shadow-none mb-2"):
                                with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                                    ui.select(
                                        [""] + tables,
                                        label="From table",
                                        value=rel.get("from_table", ""),
                                        on_change=lambda _, i=r_idx: on_relation_table_change(i),
                                    ).bind_value(rel, "from_table").classes("w-40")
                                    ui.select([""] + columns_for_table(rel.get("from_table", "")), label="From column").bind_value(rel, "from_column").classes("w-40")
                                    ui.select(
                                        [""] + tables,
                                        label="To table",
                                        value=rel.get("to_table", ""),
                                        on_change=lambda _, i=r_idx: on_relation_table_change(i),
                                    ).bind_value(rel, "to_table").classes("w-40")
                                    ui.select([""] + columns_for_table(rel.get("to_table", "")), label="To column").bind_value(rel, "to_column").classes("w-40")
                                    ui.select(["1:N", "1:1", "N:1", "N:N"], label="Cardinality").bind_value(rel, "cardinality").classes("w-28")
                                    ui.checkbox("Optional").bind_value(rel, "is_optional")
                                    ui.button(icon="delete", on_click=lambda _, i=r_idx: remove_relation_row(i)).props("flat dense round color=negative size=sm")

                    rels_for_viz = [
                        r
                        for r in local_state.get("editable_relations", [])
                        if str(r.get("from_table", "")).strip()
                        and str(r.get("from_column", "")).strip()
                        and str(r.get("to_table", "")).strip()
                        and str(r.get("to_column", "")).strip()
                    ]
                    if not rels_for_viz:
                        rels_for_viz = local_state["project_data"].get("relations", [])
                    if tables:
                        node_data = [{"name": t, "symbolSize": 42} for t in tables]
                        edge_data = []
                        for r in rels_for_viz:
                            from_t = str(r.get("from_table") or "")
                            to_t = str(r.get("to_table") or "")
                            from_c = str(r.get("from_column") or "")
                            to_c = str(r.get("to_column") or "")
                            card = str(r.get("cardinality") or "1:N")
                            if from_t and to_t:
                                edge_data.append(
                                    {
                                        "source": to_t,
                                        "target": from_t,
                                        "label": {"show": True, "formatter": f"{to_c} -> {from_c} ({card})"},
                                    }
                                )
                        if edge_data:
                            ui.label("Relationship Graph").classes("text-xs font-bold uppercase tracking-wide text-slate-500 mt-3")
                            ui.echart(
                                {
                                    "tooltip": {"trigger": "item"},
                                    "series": [
                                        {
                                            "type": "graph",
                                            "layout": "force",
                                            "roam": True,
                                            "data": node_data,
                                            "links": edge_data,
                                            "force": {"repulsion": 500, "edgeLength": [140, 200]},
                                            "label": {"show": True, "fontWeight": "bold"},
                                            "lineStyle": {"width": 2.0, "curveness": 0.16, "opacity": 0.85},
                                        }
                                    ],
                                }
                            ).classes("w-full h-64")
        elif page == "generate":
            with ui.card().classes("w-full bg-slate-50 border border-slate-200 rounded-xl p-4 shadow-none"):
                ui.label("Chat Flow: Generation").classes("text-sm font-bold text-slate-700")
                sync_generation_table_settings()
                sample_generated = bool(local_state.get("sample_generated"))
                sample_ready = bool(local_state.get("sample_confirmed"))

                if not sample_generated:
                    ui.label("Step 1: Generate a 5-row sample. It will download automatically.").classes(
                        "text-sm text-slate-500 mt-2"
                    )
                elif not sample_ready:
                    ui.label("Step 2: Approve the sample to unlock Output.").classes(
                        "text-sm text-slate-500 mt-2"
                    )
                else:
                    ui.label("Sample approved. Continue to Output for full generation.").classes("text-sm text-emerald-700 mt-2")
                ui.label(f"Status: {str(local_state.get('task_status') or 'idle').upper()}").classes("text-xs text-slate-500")
                ui.linear_progress(
                    value=max(0.0, min(1.0, float(local_state.get("task_progress") or 0) / 100.0))
                ).classes("mt-2 h-3 rounded-full")
                if local_state.get("task_logs"):
                    with ui.scroll_area().classes("h-36 w-full mt-3"):
                        for line in local_state["task_logs"][-10:]:
                            ui.label(f"> {line}").classes("text-xs mono text-slate-600")
        elif page == "output":
            with ui.card().classes("w-full bg-slate-50 border border-slate-200 rounded-xl p-4 shadow-none"):
                ui.label("Chat Flow: Output").classes("text-sm font-bold text-slate-700")
                ui.label("Review settings, launch full generation, and download the dataset.").classes(
                    "text-sm text-slate-500 mt-2"
                )
                sync_generation_table_settings()
                table_names = generation_table_names()
                selected_generation_table = str(local_state.get("selected_generation_table") or (table_names[0] if table_names else "")).strip()
                active_generation_settings = generation_settings_for(selected_generation_table)
                if table_names:
                    with ui.row().classes("w-full gap-3 flex-wrap mt-3 items-end"):
                        ui.select(
                            table_names,
                            label="Table",
                            value=selected_generation_table if selected_generation_table in table_names else table_names[0],
                            on_change=lambda e: set_selected_generation_table(str(e.value or "")),
                        ).props("outlined").classes("w-48")
                        ui.number(
                            label="Rows to generate",
                            value=int(active_generation_settings.get("num_rows") or 1),
                            on_change=lambda e: set_generation_table_rows(e.value),
                        ).props("min=1 step=100 outlined").classes("w-44")
                        ui.number(
                            label="Seed",
                            value=int(active_generation_settings.get("seed") or 42),
                            on_change=lambda e: set_generation_table_seed(e.value),
                        ).props("outlined").classes("w-36")
                    with ui.column().classes("gap-1 mt-2"):
                        ui.label("Output format").classes("text-xs font-bold uppercase tracking-wide text-slate-500")
                        ui.radio(["csv", "parquet"], value="csv").bind_value(local_state, "output_format").props("inline")

    async def refresh_correlations(table_id: str) -> None:
        if not local_state["project_id"] or not table_id or local_state["is_loading_correlation"]:
            return
        local_state["is_loading_correlation"] = True
        safe_refresh(project_view)
        try:
            async with api_client(timeout=25.0) as client:
                resp = await client.get(
                    f"{BACKEND_URL}/project/{local_state['project_id']}/tables/{table_id}/correlations",
                    params={"top_k": 30},
                )
                payload = await parse_response(resp)
            local_state["correlation_rows"] = payload.get("correlations", [])
            local_state["correlation_note"] = payload.get("note")
            local_state["association_rows"] = payload.get("associations", [])
            local_state["association_note"] = payload.get("assoc_note")
            local_state["llm_association_rows"] = payload.get("llm_associations", [])
            local_state["llm_association_note"] = payload.get("llm_note")
            llm_source = payload.get("llm_source")
            llm_model = payload.get("llm_model")
            if llm_source or llm_model:
                local_state["llm_association_meta"] = f"source: {llm_source or 'unknown'} | model: {llm_model or 'unknown'}"
            else:
                local_state["llm_association_meta"] = None
            local_state["correlation_table_id"] = table_id
        except Exception as ex:
            local_state["correlation_rows"] = []
            local_state["correlation_note"] = "Correlation unavailable."
            local_state["association_rows"] = []
            local_state["association_note"] = "Association unavailable."
            local_state["llm_association_rows"] = []
            local_state["llm_association_note"] = "LLM association unavailable."
            local_state["llm_association_meta"] = None
            safe_notify(f"Correlation lookup failed: {ex}", notify_type="negative")
        finally:
            local_state["is_loading_correlation"] = False
            safe_refresh(project_view)

    def switch_page(name: str) -> None:
        if name == "admin":
            if not is_admin_user():
                safe_notify("Admin access is required.", notify_type="warning")
                return
            local_state["page"] = "admin"
            if not local_state.get("assistant_mode_active"):
                local_state["assistant_page"] = "upload"
            app.storage.user["active_page"] = "admin"
            asyncio.create_task(load_admin_data())
            refresh_all()
            return
        if not stage_open(name):
            if name == "input":
                safe_notify("Select a setup mode first.", notify_type="warning")
            elif name == "output":
                safe_notify("Generate and approve a sample first.", notify_type="warning")
            else:
                safe_notify("Set up a project first.", notify_type="warning")
            return
        local_state["page"] = name
        if not local_state.get("assistant_mode_active"):
            local_state["assistant_page"] = name
        app.storage.user["active_page"] = name
        if name in {"project", "modeling", "generate", "output"}:
            asyncio.create_task(
                load_project(
                    refresh_plan=name in {"generate", "output"},
                    refresh_summary=name == "project",
                )
            )
        refresh_all()

    def refresh_all() -> None:
        safe_refresh(login_view)
        safe_refresh(nav_bar)
        safe_refresh(upload_view)
        safe_refresh(input_view)
        safe_refresh(project_view)
        safe_refresh(modeling_view)
        safe_refresh(generate_view)
        safe_refresh(output_view)
        safe_refresh(admin_view)
        safe_refresh(assistant_widget)

    async def handle_csv_upload(e: events.UploadEventArguments) -> None:
        if not local_state["project_id"]:
            local_state["uploaded_tables"] = []

        try:
            content = await e.file.read()
            digest = hashlib.sha1(content).hexdigest()
            recent_uploads = dict(local_state.get("recent_csv_uploads") or {})
            if digest in recent_uploads:
                safe_notify(f"{e.file.name} upload was already received. Ignoring duplicate event.", notify_type="warning")
                return
            recent_uploads[digest] = True
            local_state["recent_csv_uploads"] = recent_uploads

            local_state["multi_csv_inflight"] += 1
            row_mode = "Primary" if not local_state["project_id"] else "Additional"
            row_index = add_upload_row(e.file.name, row_mode)
            safe_refresh(input_view)

            files = {"file": (e.file.name, content, "text/csv")}
            if not local_state["project_id"]:
                async with api_client(timeout=60.0) as client:
                    resp = await client.post(f"{BACKEND_URL}/upload", files=files)
                    data = await parse_response(resp)

                local_state["project_id"] = data["project_id"]
                app.storage.user["project_id"] = data["project_id"]
                local_state["project_data"] = None
                local_state["selected_table"] = None
                local_state["generation_plan"] = None
                local_state["project_summary"] = None
                local_state["task_status"] = "idle"
                local_state["task_progress"] = 0
                local_state["task_logs"] = []
                local_state["task_file_url"] = None

                update_upload_row(
                    row_index,
                    "Uploaded",
                    "Workspace initialized",
                    table_id=data.get("table_id"),
                    project_id=data.get("project_id"),
                )
                safe_notify("Primary CSV uploaded.", notify_type="positive")
            else:
                active_project_id = str(local_state["project_id"])
                async with api_client(timeout=60.0) as client:
                    resp = await client.post(
                        f"{BACKEND_URL}/project/{active_project_id}/add-table",
                        files=files,
                    )
                    data = await parse_response(resp)
                update_upload_row(
                    row_index,
                    "Uploaded",
                    "Table added",
                    table_id=data.get("table_id"),
                    project_id=active_project_id,
                )
                safe_notify(f"{e.file.name} added to workspace.", notify_type="positive")

            await load_project(refresh_plan=True, refresh_summary=True)
            if local_state.get("assistant_mode_active") and assistant_current_page() == "input":
                push_assistant_message_if_new(
                    "assistant",
                    "CSV upload complete. Would you like to upload another file? If not, I will take you to Workspace first so you can review the project before Modeling.",
                )
        except Exception as ex:
            if "row_index" in locals():
                update_upload_row(row_index, "Failed", str(ex))
            safe_notify(f"CSV upload failed: {ex}", notify_type="negative")
        finally:
            if "digest" in locals():
                local_state["recent_csv_uploads"].pop(digest, None)
            if "row_index" in locals():
                local_state["multi_csv_inflight"] = max(0, int(local_state["multi_csv_inflight"]) - 1)
            refresh_all()

    async def handle_ddl_upload(e: events.UploadEventArguments) -> None:
        try:
            content = await e.file.read()
            files = {"file": (e.file.name, content, "application/sql")}
            params = {"dialect": local_state["dialect"]}
            async with api_client(timeout=60.0) as client:
                resp = await client.post(f"{BACKEND_URL}/upload-ddl", files=files, params=params)
                data = await parse_response(resp)
            local_state["project_id"] = data["project_id"]
            app.storage.user["project_id"] = data["project_id"]
            local_state["task_status"] = "idle"
            local_state["task_logs"] = []
            local_state["task_file_url"] = None
            local_state["uploaded_tables"] = []
            safe_notify("DDL parsed successfully. Workspace is ready.", notify_type="positive")
            switch_page("project")
        except Exception as ex:
            safe_notify(f"DDL upload failed: {ex}", notify_type="negative")

    async def create_schema_project() -> None:
        if local_state["is_submitting_schema"]:
            return

        try:
            normalized_tables: List[Dict[str, Any]] = []
            for t_idx, table in enumerate(local_state["schema_tables"], start=1):
                table_name = str(table.get("table_name") or "").strip()
                if not table_name:
                    raise ValueError(f"Table {t_idx}: table name is required.")

                cols = table.get("columns", [])
                if not cols:
                    raise ValueError(f"Table '{table_name}': add at least one column.")

                norm_cols: List[Dict[str, Any]] = []
                for c_idx, col in enumerate(cols, start=1):
                    col_name = str(col.get("name") or "").strip()
                    if not col_name:
                        raise ValueError(f"Table '{table_name}', column {c_idx}: column name is required.")

                    mandatory_raw = col.get("mandatory", True)
                    mandatory = (
                        str(mandatory_raw).strip().lower() == "yes"
                        if isinstance(mandatory_raw, str)
                        else bool(mandatory_raw)
                    )
                    norm_cols.append(
                        {
                            "name": col_name,
                            "data_type": str(col.get("data_type") or "").strip() or None,
                            "description": str(col.get("description") or "").strip() or None,
                            "mandatory": mandatory,
                            "is_unique": bool(col.get("is_unique", False)),
                            "generator_type": str(col.get("generator_type") or "").strip() or None,
                            "allowed_values": str(col.get("allowed_values") or "").strip() or None,
                            "expand_categories": bool(col.get("expand_categories", False)),
                        }
                    )

                normalized_tables.append(
                    {
                        "table_name": table_name,
                        "description": str(table.get("description") or "").strip() or None,
                        "columns": norm_cols,
                    }
                )

            local_state["is_submitting_schema"] = True
            safe_refresh(input_view)

            payload = {
                "project_name": str(local_state["schema_project_name"] or "").strip() or "DataCosmos",
                "tables": normalized_tables,
            }
            async with api_client(timeout=60.0) as client:
                resp = await client.post(f"{BACKEND_URL}/upload-schema", json=payload)
                data = await parse_response(resp)

            local_state["project_id"] = data["project_id"]
            app.storage.user["project_id"] = data["project_id"]
            local_state["task_status"] = "idle"
            local_state["task_logs"] = []
            local_state["task_file_url"] = None
            local_state["uploaded_tables"] = []
            await load_project(refresh_plan=True, refresh_summary=True)
            reset_schema_builder()

            safe_notify("Schema project created successfully.", notify_type="positive")
            if local_state.get("assistant_mode_active"):
                assistant_set_page("project")
                refresh_all()
            else:
                switch_page("project")
        except Exception as ex:
            safe_notify(f"Schema project creation failed: {ex}", notify_type="negative")
        finally:
            local_state["is_submitting_schema"] = False
            refresh_all()

    async def delete_uploaded_table(row_no: int) -> None:
        row = next((r for r in local_state["uploaded_tables"] if int(r.get("no", -1)) == int(row_no)), None)
        if not row:
            return
        if row.get("status") != "Uploaded":
            safe_notify("Only uploaded files can be deleted.", notify_type="warning")
            return

        table_id = row.get("table_id")
        project_id = row.get("project_id") or local_state["project_id"]
        if not table_id or not project_id:
            safe_notify("Cannot delete this row because table metadata is missing.", notify_type="negative")
            return

        prev_status = row.get("status", "Uploaded")
        prev_message = row.get("message", "")
        row["status"] = "Deleting"
        row["message"] = "Removing..."
        refresh_all()

        try:
            async with api_client(timeout=30.0) as client:
                resp = await client.delete(f"{BACKEND_URL}/project/{project_id}/tables/{table_id}")
                data = await parse_response(resp)

            local_state["uploaded_tables"] = [r for r in local_state["uploaded_tables"] if r is not row]
            normalize_upload_rows()

            deleted_active_project = str(project_id) == str(local_state.get("project_id"))
            if data.get("project_deleted") and deleted_active_project:
                clear_active_project_state()
                safe_notify("Project deleted because the last CSV was removed.", notify_type="warning")
            elif deleted_active_project:
                await load_project(refresh_plan=True, refresh_summary=True)
                safe_notify(f"Removed {row.get('file_name')}.", notify_type="positive")
            else:
                safe_notify(f"Removed {row.get('file_name')}.", notify_type="positive")

            refresh_all()
        except Exception as ex:
            row["status"] = prev_status
            row["message"] = prev_message
            safe_notify(f"Delete failed: {ex}", notify_type="negative")
            refresh_all()

    async def clear_all_model_tables() -> None:
        project_id = local_state.get("project_id")
        if not project_id:
            safe_notify("No active model to clear.", notify_type="warning")
            return
        try:
            async with api_client(timeout=60.0) as client:
                resp = await client.delete(f"{BACKEND_URL}/project/{project_id}")
                await parse_response(resp)
            clear_active_project_state(clear_uploaded=True)
            local_state["page"] = "upload"
            app.storage.user["active_page"] = "upload"
            safe_notify("Model cleared. You can start with a fresh CSV upload.", notify_type="positive")
            refresh_all()
        except Exception as ex:
            safe_notify(f"Clear model failed: {ex}", notify_type="negative")

    async def save_modeling(notify: bool = True) -> bool:
        if not local_state["project_data"]:
            return False
        config_list: List[Dict[str, Any]] = []
        for table in local_state["project_data"]["tables"]:
            for col in table["columns"]:
                cfg = {
                    "id": col["id"],
                    "data_type": col.get("data_type", "varchar") or "varchar",
                    "is_pii": bool(col.get("is_pii", False)),
                    "generator_type": col.get("generator_type", "auto") or "auto",
                    "allowed_values": col.get("allowed_values", "") or "",
                    "allowed_values_expanded": col.get("allowed_values_expanded", "") or "",
                    "expand_categories": bool(col.get("expand_categories", False)),
                    "randomization_pct": float(col.get("randomization_pct", 0.0) or 0.0),
                }
                
                # Forward user-editable stats if they exist
                if "null_value_percent" in col: cfg["null_value_percent"] = col["null_value_percent"]
                if "min_val" in col: cfg["min_val"] = col["min_val"]
                if "max_val" in col: cfg["max_val"] = col["max_val"]
                if "sd" in col: cfg["sd"] = col["sd"]
                if "variance" in col: cfg["variance"] = col["variance"]
                
                config_list.append(cfg)
        try:
            async with api_client(timeout=30.0) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/project/{local_state['project_id']}/config/update",
                    json={"config": config_list},
                )
                try:
                    await parse_response(resp)
                except APIError as api_ex:
                    # Backward compatibility for older backend variants that expect raw list body
                    if api_ex.status_code == 422:
                        retry_resp = await client.post(
                            f"{BACKEND_URL}/project/{local_state['project_id']}/config/update",
                            json=config_list,
                        )
                        await parse_response(retry_resp)
                    else:
                        raise
            if notify:
                safe_notify("Blueprint saved to backend.", notify_type="positive")
            local_state["sample_generated"] = False
            local_state["sample_confirmed"] = False
            return True
        except Exception as ex:
            if notify:
                safe_notify(f"Blueprint save failed: {ex}", notify_type="negative")
            return False

    async def infer_semantics_with_ai() -> None:
        if not local_state["project_id"]:
            safe_notify("Load a project first.", notify_type="warning")
            return
        if local_state["is_inferring_semantics"]:
            return

        local_state["is_inferring_semantics"] = True
        safe_refresh(modeling_view)
        try:
            async with api_client(timeout=90.0) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/project/{local_state['project_id']}/infer-semantic-types",
                    params={"apply": True},
                )
                data = await parse_response(resp)

            count = int(data.get("applied_count", 0))
            source = data.get("source", "unknown")
            model = data.get("model")
            error = data.get("error")
            details = f"{count} columns updated via {source}"
            if model:
                details += f" ({model})"
            if error:
                details += f" | fallback reason: {error}"

            safe_notify(details, notify_type="positive")
            await load_project(refresh_plan=False)
        except APIError as ex:
            if ex.status_code == 404:
                safe_notify(
                    "Semantic endpoint not found. Restart backend to load latest code.",
                    notify_type="warning",
                )
            else:
                safe_notify(f"Semantic inference failed: {ex}", notify_type="negative")
        except Exception as ex:
            safe_notify(f"Semantic inference failed: {ex}", notify_type="negative")
        finally:
            local_state["is_inferring_semantics"] = False
            safe_refresh(modeling_view)

    async def expand_categories_with_ai() -> None:
        if not local_state["project_id"]:
            safe_notify("Load a project first.", notify_type="warning")
            return []
        if local_state["is_expanding_categories"]:
            return []

        saved = await save_modeling(notify=False)
        if not saved:
            safe_notify("Save Blueprint failed, so category expansion was skipped.", notify_type="negative")
            return []

        local_state["is_expanding_categories"] = True
        safe_refresh(modeling_view)
        try:
            async with api_client(timeout=90.0) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/project/{local_state['project_id']}/expand-categories",
                    params={"apply": True, "max_values": 12},
                )
                data = await parse_response(resp)

            applied_count = int(data.get("applied_count", 0))
            safe_notify(
                f"Expanded categories for {applied_count} column(s).",
                notify_type="positive",
            )
            await load_project(refresh_plan=False)
            return data.get("expansions", [])
        except Exception as ex:
            safe_notify(f"Category expansion failed: {ex}", notify_type="negative")
            return []
        finally:
            local_state["is_expanding_categories"] = False
            safe_refresh(modeling_view)

    async def detect_pii_with_ai() -> None:
        if not local_state["project_id"]:
            safe_notify("Load a project first.", notify_type="warning")
            return
        if local_state["is_detecting_pii"]:
            return

        local_state["is_detecting_pii"] = True
        safe_refresh(modeling_view)
        try:
            async with api_client(timeout=90.0) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/project/{local_state['project_id']}/detect-pii",
                    params={"apply": True, "sample_size": 50},
                )
                data = await parse_response(resp)

            pii_count = int(data.get("pii_detected_count", 0))
            applied_count = int(data.get("applied_count", 0))
            safe_notify(
                f"PII detection complete: {pii_count} PII columns flagged ({applied_count} scanned).",
                notify_type="positive",
            )
            await load_project(refresh_plan=False)
        except APIError as ex:
            if ex.status_code == 404:
                safe_notify(
                    "PII endpoint not found. Restart backend to load latest code.",
                    notify_type="warning",
                )
            else:
                safe_notify(f"PII detection failed: {ex}", notify_type="negative")
        except Exception as ex:
            safe_notify(f"PII detection failed: {ex}", notify_type="negative")
        finally:
            local_state["is_detecting_pii"] = False
            safe_refresh(modeling_view)

    async def poll_task() -> None:
        if not local_state["task_id"]:
            return
        failures = 0
        while local_state["task_status"] == "running":
            try:
                async with api_client(timeout=10.0) as client:
                    resp = await client.get(f"{BACKEND_URL}/task/{local_state['task_id']}")
                    data = await parse_response(resp)
                local_state["task_status"] = data["status"]
                local_state["task_progress"] = data["progress"]
                local_state["task_logs"] = data["logs"]
                failures = 0
                if local_state["task_status"] == "done":
                    # Construct download URL that works for browser
                    # In Docker: convert backend:8000 to localhost:8000 for browser access
                    # In production: use the configured BACKEND_URL as-is
                    download_url = f"{BACKEND_URL}/task/{local_state['task_id']}/download"
                    
                    # For Docker development: convert internal backend hostname to localhost
                    if "backend:8000" in download_url:
                        download_url = download_url.replace("http://backend:8000", "http://localhost:8000")
                    
                    local_state["task_file_url"] = download_url
                    if local_state.get("last_generation_kind") == "sample":
                        local_state["sample_generated"] = True
                        local_state["sample_confirmed"] = False
                        asyncio.create_task(fetch_sample_preview(local_state.get("task_id")))
                        if local_state.get("task_file_url"):
                            local_state["pending_download_url"] = local_state["task_file_url"]
                refresh_all()
                if local_state["task_status"] in {"done", "failed"}:
                    break
                await asyncio.sleep(2)
            except Exception as ex:
                failures += 1
                if failures > 5:
                    local_state["task_status"] = "failed"
                    local_state["task_logs"].append(f"Polling stopped: {ex}")
                    refresh_all()
                    break
                await asyncio.sleep(3)

    async def start_generation(sample_only: bool = False) -> None:
        if not local_state["project_id"]:
            safe_notify("Select a project first.", notify_type="warning")
            return
        if not sample_only and not bool(local_state.get("sample_confirmed")):
            safe_notify("Generate and approve a sample first so you can confirm the output.", notify_type="warning")
            return
        if sample_only:
            local_state["sample_generated"] = False
            local_state["sample_confirmed"] = False
            local_state["sample_preview_tables"] = []
            local_state["sample_preview_error"] = None
        effective_rows = 5 if sample_only else int(max(1, local_state["num_rows"] or 1))
        local_state["last_generation_kind"] = "sample" if sample_only else "full"
        local_state["task_status"] = "running"
        local_state["task_progress"] = 0
        local_state["task_logs"] = ["Preparing a sample preview..." if sample_only else "Initializing generation pipeline..."]
        local_state["task_file_url"] = None
        refresh_all()

        try:
            if local_state.get("project_data"):
                saved = await save_modeling()
                if not saved:
                    raise RuntimeError("Blueprint save failed.")
            sync_generation_table_settings()
            expansions = []
            flagged_columns = [
                col
                for table in (local_state.get("project_data") or {}).get("tables", [])
                for col in table.get("columns", [])
                if bool(col.get("expand_categories", False))
            ]
            if flagged_columns and int(max(1, local_state["num_rows"])) > 0:
                local_state["task_logs"].append("Expanding categorical values before generation...")
                refresh_all()
                expansions = await expand_categories_with_ai()
                for item in expansions:
                    column_name = str(item.get("column_name") or "")
                    table_name = str(item.get("table_name") or "")
                    expanded_values = str(item.get("expanded_values") or "")
                    if expanded_values:
                        local_state["task_logs"].append(
                            f"Expanded {table_name}.{column_name}: {expanded_values}"
                        )
                refresh_all()
            raw_table_settings = dict(local_state.get("generation_table_settings") or {})
            if sample_only:
                # Force true 5-row sampling per table so backend table settings cannot override the sample size.
                sample_table_settings = {}
                for table_name in generation_table_names():
                    existing = dict(raw_table_settings.get(table_name) or {})
                    sample_table_settings[table_name] = {
                        **existing,
                        "num_rows": 5,
                    }
                table_settings_payload = sample_table_settings
            else:
                table_settings_payload = raw_table_settings

            params = {
                "num_rows": effective_rows,
                "seed": int(local_state["seed"]),
                "format": local_state["output_format"],
                "table_settings_json": json.dumps(table_settings_payload),
                "stddev_scale": float(max(0.0, local_state["stddev_scale"])),
                "variation_pct": float(max(0.0, local_state["variation_pct"])),
                "knn_smoothing": float(min(1.0, max(0.0, local_state["knn_smoothing"]))),
                "knn_neighbors": int(max(1, local_state["knn_neighbors"])),
            }
            async with api_client(timeout=60.0) as client:
                resp = await client.get(f"{BACKEND_URL}/generate/{local_state['project_id']}", params=params)
                data = await parse_response(resp)
            local_state["task_id"] = data["task_id"]
            asyncio.create_task(poll_task())
        except Exception as ex:
            local_state["task_status"] = "failed"
            local_state["task_logs"].append(f"Generation start failed: {ex}")
            local_state["task_logs"].append("Ensure backend service is running on port 8000.")
            refresh_all()

    async def save_modeling_and_open_generate() -> None:
        if local_state.get("project_data"):
            saved = await save_modeling(notify=False)
            if not saved:
                safe_notify("Please resolve modeling issues before continuing to Generate.", notify_type="warning")
                return
        switch_page("generate")

    async def navigate_with_modeling_save(name: str) -> None:
        current_page = str(local_state.get("page") or "")
        if current_page == "modeling" and name != "modeling" and local_state.get("project_data"):
            saved = await save_modeling(notify=False)
            if not saved:
                safe_notify("Please save or resolve modeling changes before leaving this step.", notify_type="warning")
                return
        local_state["profile_menu_open"] = False
        switch_page(name)

    def go_to_page(name: str) -> None:
        asyncio.create_task(navigate_with_modeling_save(name))

    async def assistant_navigate_with_save(target_page: str) -> None:
        current_page = assistant_current_page()
        if current_page == "modeling" and target_page != "modeling" and local_state.get("project_data"):
            saved = await save_modeling(notify=False)
            if not saved:
                safe_notify("Please resolve modeling issues before moving forward.", notify_type="warning")
                return
        assistant_set_page(target_page)
        if target_page in {"project", "modeling", "generate", "output"} and local_state.get("project_id"):
            await load_project(refresh_plan=target_page in {"generate", "output"}, refresh_summary=target_page == "project")

    def on_modeling_type_changed(col: Dict[str, Any], expand_box: Any) -> None:
        col["data_type"] = normalize_data_type_value(col.get("data_type"))
        sync_expand_checkbox(col, expand_box)
        safe_refresh(modeling_view)
        safe_refresh(assistant_widget)

    def on_modeling_generator_changed(col: Dict[str, Any], expand_box: Any) -> None:
        sync_expand_checkbox(col, expand_box)
        safe_refresh(modeling_view)
        safe_refresh(assistant_widget)


    def current_table() -> Optional[Dict[str, Any]]:
        if not local_state["project_data"]:
            return None
        for table in local_state["project_data"]["tables"]:
            if table["name"] == local_state["selected_table"]:
                return table
        return None

    def modeling_table_key(table: Dict[str, Any]) -> str:
        return str(table.get("id") or table.get("name") or "")

    def is_modeling_table_collapsed(table: Dict[str, Any]) -> bool:
        collapsed = local_state.get("modeling_table_collapsed") or {}
        return bool(collapsed.get(modeling_table_key(table), False))

    def toggle_modeling_table(table: Dict[str, Any]) -> None:
        key = modeling_table_key(table)
        collapsed = dict(local_state.get("modeling_table_collapsed") or {})
        collapsed[key] = not bool(collapsed.get(key, False))
        local_state["modeling_table_collapsed"] = collapsed
        safe_refresh(modeling_view)
        safe_refresh(assistant_widget)

    def render_modeling_table_editor(table: Dict[str, Any], *, compact: bool = False) -> None:
        width_class = "w-56" if compact else "w-64"
        stat_class = "w-20" if compact else "w-24"
        variance_class = "w-24" if compact else "w-28"
        date_stat_class = "w-28" if compact else "w-32"

        def render_date_bound_input(col: Dict[str, Any], key: str, label: str) -> None:
            initial = _normalized_date_text(col.get(key))
            with ui.row().classes(f"{date_stat_class} items-end no-wrap gap-1"):
                date_input = ui.input(label, value=initial).classes("grow min-w-0")
                date_input.props("dense")
                date_input.on_value_change(lambda e, c=col, k=key: c.__setitem__(k, str(e.value or "").strip()))
                with ui.menu() as picker_menu:
                    picker = ui.date(value=initial or None).props("mask=YYYY-MM-DD")
                    picker.on_value_change(
                        lambda e, c=col, k=key, inp=date_input, m=picker_menu: (
                            c.__setitem__(k, str(e.value or "").strip()),
                            inp.set_value(str(e.value or "").strip()),
                            m.close(),
                        )
                    )
                ui.button(icon="event", on_click=lambda m=picker_menu: m.open()).props("flat dense round size=sm").classes(
                    "mb-1"
                )
        collapsed = is_modeling_table_collapsed(table)
        with ui.card().classes("glass-panel p-0 overflow-hidden w-full"):
            header_row = ui.row().classes(
                "w-full px-4 py-2 items-center justify-between gap-3 text-white cursor-pointer select-none"
            ).style("background-color: var(--nexus-brand);")
            header_row.on("click", lambda _, t=table: toggle_modeling_table(t))
            with header_row:
                with ui.row().classes("items-center gap-3"):
                    ui.label(f"TABLE: {table['name']}").classes("font-bold text-sm tracking-wider")
                    ui.label(f"{len(table.get('columns', []))} columns").classes("text-xs font-semibold text-slate-100")
                ui.icon("expand_more" if collapsed else "expand_less").classes("ml-auto text-white")
            def render_editor_rows() -> None:
                with ui.column().classes("p-4 gap-1"):
                    for col in table.get("columns", []):
                        with ui.column().classes("w-full hover:bg-slate-50 rounded p-2 gap-1"):
                            with ui.row().classes("w-full items-center justify-between gap-4 flex-wrap"):
                                with ui.column().classes("gap-0 min-w-[120px]"):
                                    ui.label(col["name"]).classes("font-semibold text-slate-700")
                                    ui.label(col.get("data_type") or "UNKNOWN").classes("text-xs text-slate-400")
                                with ui.row().classes("items-center gap-4 flex-wrap"):
                                    ui.checkbox("PII").bind_value(col, "is_pii")
                                    expand_box = ui.checkbox("Expand").bind_value(col, "expand_categories")
                                    type_select = ui.select(SCHEMA_TYPE_OPTIONS, label="Type").bind_value(col, "data_type").classes("w-36")
                                    generator_select = ui.select(
                                        ["auto", "categorical", "integer", "numerical", "datetime"],
                                        label="Generator",
                                    ).bind_value(col, "generator_type").classes("w-44")
                                    generator_select.on_value_change(lambda _, c=col, cb=expand_box: on_modeling_generator_changed(c, cb))
                                    type_select.on_value_change(lambda _, c=col, cb=expand_box: on_modeling_type_changed(c, cb))
                                    sync_expand_checkbox(col, expand_box)
                                    allowed_values_input = ui.input(
                                        "Allowed Values",
                                        placeholder="e.g. Sales, HR, Finance or one per line",
                                    ).bind_value(col, "allowed_values").classes(width_class)
                                    attach_tooltip(
                                        allowed_values_input,
                                        "Separate allowed values with commas or new lines, for example Sales, HR, Finance.",
                                    )
                                    ui.label("Example: Sales, HR, Finance or one value per line.").classes("text-[11px] text-slate-400")

                            with ui.row().classes("w-full items-center gap-2 mt-1 flex-wrap"):
                                ui.number("Null %", format="%.2f").bind_value(col, "null_value_percent").classes(stat_class)
                                is_numeric = _column_is_numeric(col)
                                is_datetime = _column_is_datetime(col)
                                if is_numeric:
                                    min_input = ui.number("Min").bind_value(col, "min_val").classes(stat_class)
                                    max_input = ui.number("Max").bind_value(col, "max_val").classes(stat_class)
                                    sd_input = ui.number("Std Dev", format="%.4f").bind_value(col, "sd").classes(variance_class)
                                    var_input = ui.number("Variance", format="%.4f").bind_value(col, "variance").classes(variance_class)
                                    for field in (min_input, max_input, sd_input, var_input):
                                        field.set_enabled(True)
                                elif is_datetime:
                                    render_date_bound_input(col, "min_val", "Min")
                                    render_date_bound_input(col, "max_val", "Max")
                                    sd_input = ui.input("Std Dev").classes(variance_class)
                                    var_input = ui.input("Variance").classes(variance_class)
                                    sd_input.set_value("" if col.get("sd") is None else str(col.get("sd")))
                                    var_input.set_value("" if col.get("variance") is None else str(col.get("variance")))
                                    sd_input.set_enabled(False)
                                    var_input.set_enabled(False)
                                else:
                                    ui.input("Min").bind_value(col, "min_val").classes(stat_class)
                                    ui.input("Max").bind_value(col, "max_val").classes(stat_class)
                                    sd_input = ui.input("Std Dev").classes(variance_class)
                                    var_input = ui.input("Variance").classes(variance_class)
                                    sd_input.set_value("" if col.get("sd") is None else str(col.get("sd")))
                                    var_input.set_value("" if col.get("variance") is None else str(col.get("variance")))
                                    sd_input.set_enabled(False)
                                    var_input.set_enabled(False)
                            ui.separator().classes("opacity-30")

            if not collapsed:
                if compact:
                    with ui.scroll_area().classes("w-full").style("height: 32rem;"):
                        render_editor_rows()
                else:
                    render_editor_rows()

    def table_names_in_project() -> List[str]:
        if not local_state["project_data"]:
            return []
        return [str(t.get("name")) for t in local_state["project_data"].get("tables", [])]

    def columns_for_table(table_name: str) -> List[str]:
        if not local_state["project_data"]:
            return []
        for table in local_state["project_data"].get("tables", []):
            if str(table.get("name")) == str(table_name):
                return [str(c.get("name")) for c in table.get("columns", [])]
        return []

    def _normalize_relation_row(row: Dict[str, Any]) -> Dict[str, Any]:
        row = {
            "from_table": str(row.get("from_table") or ""),
            "from_column": str(row.get("from_column") or ""),
            "to_table": str(row.get("to_table") or ""),
            "to_column": str(row.get("to_column") or ""),
            "cardinality": str(row.get("cardinality") or "1:N").upper(),
            "is_optional": bool(row.get("is_optional", True)),
        }
        if row["cardinality"] not in {"1:N", "1:1", "N:1", "N:N"}:
            row["cardinality"] = "1:N"
        return row

    def _infer_generator_from_dtype(data_type: str) -> str:
        dtype = str(data_type or "").upper()
        if any(t in dtype for t in ["DATE", "TIME"]):
            return "datetime"
        if any(t in dtype for t in ["INT", "BIGINT", "SMALLINT"]):
            return "integer"
        if any(t in dtype for t in ["NUM", "DEC", "DOUBLE", "FLOAT", "REAL"]):
            return "numerical"
        return "categorical"

    def _coerce_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"none", "nan", "null"}:
            return None
        try:
            return float(text)
        except Exception:
            return None

    def _column_is_numeric(col: Dict[str, Any]) -> bool:
        return col.get("generator_type") in ("integer", "numerical") or any(
            token in str(col.get("data_type") or "").upper() for token in ["INT", "NUM", "DEC", "DOUBLE", "FLOAT", "REAL"]
        )

    def _column_is_datetime(col: Dict[str, Any]) -> bool:
        dtype = str(col.get("data_type") or "").upper()
        return col.get("generator_type") == "datetime" or any(token in dtype for token in ["DATE", "TIME", "TIMESTAMP", "DATETIME"])

    def _normalized_date_text(value: Any) -> str:
        text = str(value or "").strip()
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            return text[:10]
        return text

    def normalize_project_columns() -> None:
        project_data = local_state.get("project_data") or {}
        for table in project_data.get("tables", []):
            for col in table.get("columns", []):
                col["data_type"] = normalize_data_type_value(col.get("data_type"))
                col["generator_type"] = str(col.get("generator_type") or "auto").strip().lower() or "auto"
                col["allowed_values"] = str(col.get("allowed_values") or "").strip()
                col["allowed_values_expanded"] = str(col.get("allowed_values_expanded") or "").strip()
                col["null_value_percent"] = _coerce_float(col.get("null_value_percent")) or 0.0
                col["sd"] = _coerce_float(col.get("sd"))
                col["variance"] = _coerce_float(col.get("variance"))
                if _column_is_numeric(col):
                    col["min_val"] = _coerce_float(col.get("min_val"))
                    col["max_val"] = _coerce_float(col.get("max_val"))
                else:
                    col["min_val"] = "" if col.get("min_val") in (None, "None") else str(col.get("min_val") or "")
                    col["max_val"] = "" if col.get("max_val") in (None, "None") else str(col.get("max_val") or "")

    def dedupe_project_tables() -> None:
        project_data = local_state.get("project_data") or {}
        tables = list(project_data.get("tables") or [])
        unique_tables: List[Dict[str, Any]] = []
        seen_signatures: set[tuple[Any, ...]] = set()
        for table in tables:
            signature = (
                str(table.get("name") or ""),
                int(table.get("row_count") or 0),
                tuple(
                    (str(col.get("name") or ""), str(col.get("data_type") or ""))
                    for col in (table.get("columns") or [])
                ),
            )
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            unique_tables.append(table)
        project_data["tables"] = unique_tables

    def _ensure_relation_columns(row: Dict[str, Any]) -> None:
        from_cols = columns_for_table(row.get("from_table", ""))
        to_cols = columns_for_table(row.get("to_table", ""))
        if row.get("from_column") not in from_cols:
            row["from_column"] = from_cols[0] if from_cols else ""
        if row.get("to_column") not in to_cols:
            row["to_column"] = to_cols[0] if to_cols else ""

    def sync_editable_relations_from_project() -> None:
        if not local_state["project_data"]:
            local_state["editable_relations"] = []
            return
        rels = local_state["project_data"].get("relations", [])
        local_state["editable_relations"] = [_normalize_relation_row(r) for r in rels]
        for row in local_state["editable_relations"]:
            _ensure_relation_columns(row)

    def add_relation_row() -> None:
        tables = table_names_in_project()
        if len(tables) < 2:
            safe_notify("Need at least 2 tables to add a relationship.", notify_type="warning")
            return
        row = {
            "from_table": "",
            "to_table": "",
            "from_column": "",
            "to_column": "",
            "cardinality": "1:N",
            "is_optional": True,
        }
        local_state["editable_relations"].insert(0, row)
        safe_refresh(modeling_view)

    def remove_relation_row(index: int) -> None:
        if 0 <= index < len(local_state["editable_relations"]):
            local_state["editable_relations"].pop(index)
            safe_refresh(modeling_view)

    def on_relation_table_change(index: int) -> None:
        if 0 <= index < len(local_state["editable_relations"]):
            row = local_state["editable_relations"][index]
            row["from_column"] = ""
            row["to_column"] = ""
            safe_refresh(modeling_view)

    async def infer_relationships_with_ai() -> None:
        if not local_state["project_id"]:
            safe_notify("Load a project first.", notify_type="warning")
            return
        if local_state["is_inferring_relations"]:
            return
        local_state["is_inferring_relations"] = True
        safe_refresh(modeling_view)
        try:
            async with api_client(timeout=90.0) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/project/{local_state['project_id']}/infer-relations",
                    params={"apply": True},
                )
                data = await parse_response(resp)
            applied = int(data.get("applied_count", 0))
            source = str(data.get("source") or "unknown")
            safe_notify(f"Inferred {applied} relationship(s) via {source}.", notify_type="positive")
            await load_project(refresh_plan=False, refresh_summary=False)
        except Exception as ex:
            safe_notify(f"Relationship inference failed: {ex}", notify_type="negative")
        finally:
            local_state["is_inferring_relations"] = False
            safe_refresh(modeling_view)

    async def save_relationships() -> None:
        if not local_state["project_id"]:
            safe_notify("Load a project first.", notify_type="warning")
            return
        if local_state["is_saving_relations"]:
            return

        payload_rows = []
        for row in local_state["editable_relations"]:
            norm = _normalize_relation_row(row)
            if not norm["from_table"] or not norm["from_column"] or not norm["to_table"] or not norm["to_column"]:
                continue
            payload_rows.append(norm)

        local_state["is_saving_relations"] = True
        safe_refresh(modeling_view)
        try:
            async with api_client(timeout=60.0) as client:
                resp = await client.post(
                    f"{BACKEND_URL}/project/{local_state['project_id']}/relations/update",
                    json={"relations": payload_rows},
                )
                data = await parse_response(resp)
            updated = int(data.get("updated_count", 0))
            safe_notify(f"Saved {updated} relationship(s).", notify_type="positive")
            await load_project(refresh_plan=False, refresh_summary=False)
        except Exception as ex:
            safe_notify(f"Save relationships failed: {ex}", notify_type="negative")
        finally:
            local_state["is_saving_relations"] = False
            safe_refresh(modeling_view)

    @ui.refreshable
    def nav_bar() -> None:
        user = auth_user()
        with ui.row().classes("w-full glass-panel p-3 px-4 mb-4 items-center justify-between gap-4 fade-up"):
            with ui.column().classes("gap-0"):
                brand = ui.button("DataCosmos", on_click=lambda: go_to_page("upload")).props("flat no-caps")
                brand.classes("text-h5 font-extrabold px-0 py-0 min-h-0")
                brand.style("color: var(--nexus-brand);")
                #ui.label("Synthetic data pipeline with relational guarantees").classes("text-xs text-slate-500")
            with ui.row().classes("flex-wrap gap-2 justify-end"):
                if local_state.get("page") != "admin":
                    for idx, step in enumerate(STEPS):
                        classes = "stage-pill"
                        if local_state["page"] == step["key"]:
                            classes += " active"
                        button = ui.button(
                            f"{idx + 1}. {step['label']}",
                            on_click=lambda name=step["key"]: go_to_page(name),
                        ).props("flat no-caps")
                        button.classes(classes)
                        button.set_enabled(stage_open(step["key"]))
                if user.get("username"):
                    if local_state.get("page") == "admin":
                        action_button(
                            "Back to Setup",
                            icon="arrow_back",
                            on_click=lambda: go_to_page("upload"),
                            variant="outline",
                            compact=True,
                        )
                    with ui.element("div").classes("inline-flex items-center"):
                        with ui.button(icon="account_circle", on_click=toggle_profile_menu).props("round unelevated") as user_btn:
                            user_btn.classes("assistant-choice")

        if user.get("username") and local_state.get("profile_menu_open"):
            with ui.element("div").style(
                "position: fixed; top: 92px; right: 28px; z-index: 5000; pointer-events: auto;"
            ):
                with ui.card().classes("glass-panel p-4 w-[220px]").style(
                    "position: relative; z-index: 5001;"
                ):
                    ui.label(str(user.get("username") or "User")).classes("text-base font-bold").style(
                        "color: var(--nexus-brand);"
                    )
                    ui.label("Signed in").classes("text-xs text-slate-500 mb-2")
                    if is_admin_user() and local_state.get("page") != "admin":
                        action_button(
                            "Admin Access",
                            icon="admin_panel_settings",
                            on_click=lambda: (close_profile_menu(), go_to_page("admin")),
                            variant="outline",
                            compact=True,
                        ).classes("w-full")
                    action_button(
                        "Logout",
                        icon="logout",
                        on_click=lambda: (close_profile_menu(), asyncio.create_task(submit_logout())),
                        variant="outline",
                        compact=True,
                    ).classes("w-full")

    @ui.refreshable
    def login_view() -> None:
        if local_state.get("auth_token"):
            return
        with ui.column().classes("w-full max-w-md mx-auto mt-24 gap-6 fade-up"):
            with ui.card().classes("glass-panel p-8 w-full"):
                ui.label("Sign In To DataCosmos").classes("text-h5 font-extrabold mb-2").style("color: var(--nexus-brand);")
                ui.label("Use your app credentials to access uploads, modeling, generation, and downloads.").classes(
                    "text-sm text-slate-600 mb-4"
                )
                username = ui.input("Username").bind_value(local_state, "login_username").props("outlined")
                username.classes("w-full")
                password = ui.input("Password", password=True, password_toggle_button=True).bind_value(
                    local_state, "login_password"
                ).props("outlined")
                password.classes("w-full")
                password.on("keydown.enter", lambda _: asyncio.create_task(submit_login()))
                if local_state.get("login_error"):
                    ui.label(str(local_state.get("login_error"))).classes("text-sm text-red-500")
                sign_in_btn = action_button(
                    "Sign In",
                    icon="login",
                    on_click=lambda: asyncio.create_task(submit_login()),
                    variant="primary",
                )
                sign_in_btn.set_enabled(not local_state.get("auth_busy"))

    @ui.refreshable
    def upload_view() -> None:
        if local_state["page"] != "upload":
            return
        with ui.column().classes("w-full gap-6 fade-up"):
            modes = [
                {
                    "key": "csv",
                    "title": "CSV Ingestion",
                    "desc": "Upload one or more CSV files and synthesize from observed patterns.",
                    "hint": "Best for profile-driven synthesis from real data.",
                    "accent": "#0369a1",
                },
                {
                    "key": "ddl",
                    "title": "DDL Blueprint",
                    "desc": "Upload SQL DDL and generate relational mock data from structure.",
                    "hint": "Best when you have schema files but no source data.",
                    "accent": "#b45309",
                },
                {
                    "key": "schema",
                    "title": "Schema Studio",
                    "desc": "Define tables/columns manually (name required, type/description optional).",
                    "hint": "Best for rapid schema-first prototyping.",
                    "accent": "#047857",
                },
            ]
            with ui.row().classes("w-full gap-6 flex-wrap items-stretch"):
                for mode in modes:
                    selected = local_state["setup_mode"] == mode["key"]
                    border_style = f"border: 2px solid {mode['accent']};" if selected else "border: 1px solid #d6e1ea;"
                    with ui.card().classes("glass-panel lift p-6 w-full flex-1 min-w-[320px] max-w-md").style(border_style):
                        ui.label(mode["title"]).classes("text-h6 font-bold mb-1").style(f"color: {mode['accent']};")
                        ui.label(mode["desc"]).classes("text-sm text-slate-600 mb-2")
                        ui.label(mode["hint"]).classes("text-xs text-slate-400")
                        state_text = "Selected" if selected else "Select this mode"
                        pick_btn = action_button(
                            state_text,
                            icon="check_circle" if selected else "radio_button_unchecked",
                            on_click=lambda m=mode["key"]: select_setup_mode(m),
                            variant="outline",
                            compact=True,
                        )
                        pick_btn.classes("mt-4")
                        if selected:
                            pick_btn.set_enabled(False)

            with ui.row().classes(ACTION_BAR):
                if local_state["setup_mode"]:
                    ui.label(f"Selected mode: {local_state['setup_mode'].upper()}").classes("text-sm text-slate-600")
                go_btn = action_button(
                    "Continue to Input",
                    icon="arrow_forward",
                    on_click=lambda: go_to_page("input"),
                    variant="primary",
                )
                go_btn.set_enabled(bool(local_state["setup_mode"]))

    @ui.refreshable
    def input_view() -> None:
        if local_state["page"] != "input":
            return
        with ui.column().classes("w-full gap-6 fade-up"):
            selected_mode = local_state.get("setup_mode") or ""
            if not selected_mode:
                with ui.card().classes("glass-panel p-6"):
                    ui.label("No mode selected.").classes("text-sm text-slate-600")
                    action_button("Back to Setup", icon="arrow_back", on_click=lambda: go_to_page("upload"), variant="outline")
                return

            mode_titles = {"csv": "CSV Ingestion", "ddl": "DDL Blueprint", "schema": "Schema Studio"}
            render_page_header(
                f"Add The Inputs For {mode_titles.get(selected_mode, selected_mode.upper())}",
                "Complete this step first, then review the outcome in Workspace before moving ahead.",
            )
            with ui.row().classes("w-full justify-between items-center flex-wrap gap-2"):
                ui.label("Need another method? Return to Setup and switch mode.").classes("text-xs text-slate-500")
                action_button("Change Mode", icon="tune", on_click=lambda: go_to_page("upload"), variant="outline", compact=True)

            if selected_mode == "csv":
                with ui.card().classes("glass-panel lift p-6 w-full max-w-3xl"):
                    ui.label("CSV Ingestion").classes("text-h5 font-bold text-sky-900 mb-1 setup-section-title")
                    ui.label("Upload real data and synthesize from observed patterns.").classes("text-sm text-slate-500 mb-3")
                    ui.upload(on_upload=handle_csv_upload, label="Drop CSV file", auto_upload=True).props(
                        "accept=.csv,text/csv"
                    ).classes("w-full upload-zone")
                    uploaded_count = uploaded_success_count()
                    with ui.row().classes("setup-stats"):
                        ui.label(f"Uploaded: {uploaded_count}").classes("setup-chip")
                        ui.label(
                            f"Active model: {'Ready' if local_state['project_id'] else 'Not created'}"
                        ).classes("setup-chip")
                    ui.label("First file creates the model. Next files are appended as additional tables.").classes(
                        "text-xs text-slate-500 mt-2"
                    )
                    if local_state["multi_csv_inflight"] > 0:
                        with ui.row().classes("w-full justify-start items-center gap-2 mt-2 text-sky-700"):
                            ui.spinner(size="sm")
                            ui.label(f"Processing {local_state['multi_csv_inflight']} file(s)...")
                    if local_state["uploaded_tables"]:
                        with ui.card().classes("upload-file-shell p-2 mt-3"):
                            ui.label("Uploaded Files").classes("text-sm font-bold text-slate-700 mb-1")
                            with ui.column().classes("w-full gap-2"):
                                with ui.row().classes("w-full text-xs text-slate-500 font-bold px-2 upload-table-head"):
                                    ui.label("#").classes("text-center")
                                    ui.label("File")
                                    ui.label("Type")
                                    ui.label("Status")
                                    ui.label("Action").classes("text-center")
                                for row in local_state["uploaded_tables"]:
                                    with ui.row().classes(
                                        "w-full bg-white/60 rounded border border-slate-100 px-2 py-1 upload-table-row"
                                    ):
                                        ui.label(str(row["no"])).classes("text-xs text-slate-600 text-center")
                                        ui.label(str(row["file_name"])).classes("text-xs text-slate-700 cell-truncate")
                                        ui.label(str(row["mode"])).classes("text-xs text-slate-600")
                                        ui.label(str(row["status"])).classes("text-xs text-slate-700")
                                        delete_btn = ui.button(icon="delete").props("flat dense round color=negative size=sm")
                                        delete_btn.classes("justify-self-center")
                                        delete_btn.on_click(lambda _, row_no=row["no"]: asyncio.create_task(delete_uploaded_table(row_no)))
                                        delete_btn.set_enabled(str(row.get("status", "")) == "Uploaded")
                    ui.label("Best for profile-driven single or multi-table synthesis.").classes("text-xs text-slate-400 mt-3")

                with ui.row().classes(ACTION_BAR):
                    open_workspace_btn = action_button(
                        "Open Workspace",
                        icon="workspaces",
                        on_click=lambda: go_to_page("project"),
                        variant="primary",
                    )
                    open_workspace_btn.set_enabled(bool(local_state["project_id"]) and local_state["multi_csv_inflight"] == 0)

            elif selected_mode == "ddl":
                with ui.card().classes("glass-panel lift p-6 w-full max-w-3xl"):
                    ui.label("DDL Blueprint").classes("text-h5 font-bold text-amber-800 mb-1")
                    ui.label("Upload SQL DDL to create a workspace from schema structure.").classes("text-sm text-slate-500 mb-3")
                    ui.select(
                        ["postgres", "mysql", "sqlite", "sqlserver", "oracle"],
                        label="DDL dialect",
                    ).bind_value(local_state, "dialect").classes("w-full max-w-xs mb-3")
                    ui.upload(on_upload=handle_ddl_upload, label="Drop DDL file", auto_upload=True).props(
                        "accept=.sql,text/plain,application/sql"
                    ).classes("w-full upload-zone")
                    ui.label("Best when the schema already exists and source CSV files are not available.").classes("text-xs text-slate-400 mt-3")

            else:
                with ui.card().classes("glass-panel lift p-6 w-full max-w-5xl"):
                    ui.label("Schema Studio").classes("text-h5 font-bold text-emerald-900 mb-1")
                    ui.label("Build schema manually with multiple tables and columns.").classes("text-sm text-slate-500 mb-3")
                    project_name_input = ui.input(label="Project name (optional)").bind_value(local_state, "schema_project_name").classes("w-full mb-4")
                    attach_tooltip(project_name_input, "Optional label for this schema project. Leave blank to use an auto-generated name.")

                    with ui.row().classes("w-full gap-4 flex-wrap"):
                        with ui.column().classes("w-full max-w-sm gap-3"):
                            with ui.card().classes("bg-white/70 border border-slate-200 rounded-lg p-3"):
                                with ui.row().classes("w-full items-center justify-between gap-2 mb-2"):
                                    ui.label("Tables").classes("text-sm font-bold text-slate-700")
                                    add_table_btn = action_button(
                                        "Add Table",
                                        icon="add_box",
                                        on_click=add_schema_table,
                                        variant="outline",
                                        compact=True,
                                    )
                                    attach_tooltip(add_table_btn, "Create another table in the schema studio project.")
                                if not local_state["schema_tables"]:
                                    ui.label("No tables yet. Add your first table to begin.").classes(
                                        "text-xs text-slate-500"
                                    )
                                for t_idx, table in enumerate(local_state["schema_tables"]):
                                    is_active = int(local_state.get("schema_active_table_idx", 0)) == t_idx
                                    table_title = str(table.get("table_name") or "").strip() or f"Table {t_idx + 1} (unnamed)"
                                    cols = table.get("columns", [])
                                    col_count = len(cols)
                                    mandatory_count = sum(
                                        1
                                        for c in cols
                                        if (
                                            str(c.get("mandatory", True)).strip().lower() == "yes"
                                            if isinstance(c.get("mandatory", True), str)
                                            else bool(c.get("mandatory", True))
                                        )
                                    )

                                    with ui.card().classes(
                                        f"schema-table-item p-3 {'active' if is_active else ''}"
                                    ):
                                        with ui.row().classes("w-full items-center justify-between gap-2"):
                                            with ui.column().classes("gap-1 min-w-0"):
                                                ui.label(table_title).classes("text-sm font-bold text-slate-700 cell-truncate")
                                                with ui.row().classes("items-center gap-2"):
                                                    ui.label(f"{col_count} cols").classes("schema-meta-chip")
                                                    ui.label(f"{mandatory_count} mandatory").classes("schema-meta-chip")
                                            with ui.row().classes("items-center gap-1"):
                                                edit_btn = ui.button(
                                                    "Editing" if is_active else "Edit",
                                                    icon="edit",
                                                ).props("flat dense no-caps color=primary")
                                                attach_tooltip(edit_btn, "Open this table so you can add or edit its columns.")
                                                edit_btn.on_click(lambda _, i=t_idx: set_active_schema_table(i))
                                                if is_active:
                                                    edit_btn.set_enabled(False)
                                                rm_tbl_btn = ui.button(icon="delete").props("flat dense round color=negative size=sm")
                                                attach_tooltip(rm_tbl_btn, "Remove this table from the schema studio project.")
                                                rm_tbl_btn.on_click(lambda _, i=t_idx: remove_schema_table(i))
                                                rm_tbl_btn.set_enabled(len(local_state["schema_tables"]) > 1)

                                        preview_desc = str(table.get("description") or "").strip()
                                        if preview_desc:
                                            ui.label(preview_desc).classes("text-xs text-slate-500 mt-1 cell-truncate")

                        with ui.column().classes("flex-1 gap-3 min-w-[20rem]"):
                            active_index = int(local_state.get("schema_active_table_idx", 0))
                            active_table: Optional[Dict[str, Any]] = None
                            if 0 <= active_index < len(local_state["schema_tables"]):
                                active_table = local_state["schema_tables"][active_index]

                            if not active_table:
                                ui.label("Select a table to edit.").classes("text-sm text-slate-500")
                            else:
                                with ui.card().classes("bg-white/70 border border-slate-200 rounded-lg p-4"):
                                    ui.label("Table Editor").classes("text-sm font-bold text-slate-700 mb-2")
                                    table_name_input = ui.input("Table name *").bind_value(active_table, "table_name").classes("w-full mb-2")
                                    attach_tooltip(table_name_input, "Unique table name to create in the generated schema, for example orders, customers, or products.")
                                    table_description_input = ui.input("Table description").bind_value(active_table, "description").classes("w-full mb-2")
                                    attach_tooltip(table_description_input, "Optional description of what this table represents.")
                                    ui.label("Columns").classes("text-xs font-semibold text-slate-600 mb-2")

                                    cols = active_table.get("columns", [])
                                    for c_idx, col in enumerate(cols):
                                        with ui.card().classes("w-full bg-white/60 border border-slate-100 rounded-lg p-3"):
                                            with ui.row().classes("w-full items-start justify-between gap-2"):
                                                with ui.column().classes("flex-1 gap-3 min-w-0"):
                                                    with ui.row().classes("w-full items-end gap-3 flex-wrap"):
                                                        column_name_input = ui.input("Column name *").bind_value(col, "name").classes("flex-1 min-w-[220px]")
                                                        attach_tooltip(column_name_input, "Column name used in the generated table, for example order_id, prod_name, or created_at.")
                                                        schema_type_input = ui.select(
                                                            SCHEMA_TYPE_OPTIONS,
                                                            label="Type",
                                                        ).bind_value(col, "data_type").classes("w-36")
                                                        attach_tooltip(schema_type_input, "Storage type for the column in DuckDB. Choose from common types such as varchar, integer, decimal, date, or timestamp.")
                                                        generator_select = ui.select(
                                                            ["auto", "categorical", "integer", "numerical", "datetime"],
                                                            label="Generator",
                                                        ).bind_value(col, "generator_type").classes("w-44")
                                                        attach_tooltip(generator_select, "Controls how synthetic values are generated. Use categorical for lists, numerical for measures, and auto when the system should infer behavior.")
                                                    with ui.row().classes("w-full items-end gap-3 flex-wrap"):
                                                        allowed_values_input = ui.input("Allowed Values", placeholder="e.g. Sales, HR, Finance or one per line").bind_value(col, "allowed_values").classes("flex-1 min-w-[260px]")
                                                        attach_tooltip(allowed_values_input, "Enter comma-separated seed values. For category columns, keep these broad and few, such as Accessories, Furniture, Apparel. For product columns, list concrete items such as desk, couch, sweater.")
                                                        description_input = ui.input("Description").bind_value(col, "description").classes("flex-1 min-w-[240px]")
                                                        attach_tooltip(description_input, "Optional note about the column meaning or how it should be generated.")
                                                    with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                                                        mandatory_box = ui.checkbox("Mandatory").bind_value(col, "mandatory")
                                                        attach_tooltip(mandatory_box, "Turn this on if the column must always have a value. When off, the generator may leave some rows blank.")
                                                        unique_box = ui.checkbox("Unique Values").bind_value(col, "is_unique")
                                                        attach_tooltip(unique_box, "Turn this on when every generated value should be distinct, such as order numbers, usernames, or reference codes.")
                                                        expand_box = ui.checkbox("Expand Categories").bind_value(col, "expand_categories")
                                                        attach_tooltip(expand_box, "Use AI or fallback expansion to widen the allowed-values list for categorical columns before generation.")
                                                        generator_select.on_value_change(lambda _, c=col, cb=expand_box: sync_expand_checkbox(c, cb))
                                                        sync_expand_checkbox(col, expand_box)
                                                rm_col_btn = ui.button(icon="delete").props("flat dense round color=negative size=sm")
                                                attach_tooltip(rm_col_btn, "Remove this column from the table.")
                                                rm_col_btn.on_click(lambda _, ti=active_index, ci=c_idx: remove_schema_column(ti, ci))
                                                rm_col_btn.set_enabled(len(cols) > 1)

                                    with ui.row().classes("w-full justify-end mt-2"):
                                        add_col_btn = action_button(
                                            "Add Column",
                                            icon="add",
                                            on_click=lambda ti=active_index: add_schema_column(ti),
                                            variant="outline",
                                            compact=True,
                                        )
                                        attach_tooltip(add_col_btn, "Append another column to this table.")

                    with ui.row().classes("w-full items-center justify-between gap-2 mt-4 flex-wrap"):
                        ui.label("Column name is mandatory. Type/description are optional.").classes("text-xs text-slate-400")
                        create_schema_btn = action_button(
                            "Create Schema Project",
                            icon="schema",
                            on_click=lambda: asyncio.create_task(create_schema_project()),
                            variant="success",
                            compact=True,
                        )
                        create_schema_btn.set_enabled(not local_state["is_submitting_schema"])

    @ui.refreshable
    def project_view() -> None:
        if local_state["page"] != "project":
            return
        if local_state["is_loading_project"] and not local_state["project_data"]:
            with ui.row().classes("w-full justify-center py-24"):
                ui.spinner(size="lg")
            return
        if not local_state["project_data"]:
            ui.label("No project loaded.").classes("text-slate-500")
            return

        data = local_state["project_data"]
        project = data["project"]
        tables = data["tables"]
        relations = data["relations"]

        with ui.column().classes("w-full gap-4 fade-up"):
            render_page_header(
                f"Review The Workspace",
                "Use this step to understand tables, data profiles, and relationships before you fine-tune generation behavior.",
            )

            with ui.card().classes("glass-panel p-5"):
                with ui.row().classes("w-full items-center justify-between gap-2 mb-2"):
                    summary_hdr = ui.label("Project Summary").classes("text-sm font-bold text-slate-700")
                    attach_tooltip(summary_hdr, "High-level AI summary of the current project structure and intent.")
                    refresh_summary_btn = action_button(
                        "Refresh AI Summary",
                        icon="auto_awesome",
                        on_click=lambda: asyncio.create_task(refresh_project_summary(show_notify=True)),
                        variant="outline",
                        compact=True,
                    )
                    refresh_summary_btn.set_enabled(not local_state["is_loading_summary"])

                if local_state["is_loading_summary"]:
                    with ui.row().classes("items-center gap-2 text-amber-700"):
                        ui.spinner(size="sm")
                        ui.label("Generating concise summary...")
                elif local_state["project_summary"] and local_state["project_summary"].get("summary"):
                    ui.label(local_state["project_summary"]["summary"]).classes("text-base leading-6 text-slate-700")
                else:
                    ui.label("Summary unavailable. Click refresh to generate one.").classes("text-sm text-slate-500 italic")

            with ui.row().classes("w-full gap-3 flex-wrap"):
                with ui.card().classes("glass-panel p-4 min-w-40"):
                    tables_hdr = ui.label("Tables").classes("text-xs text-slate-500")
                    attach_tooltip(tables_hdr, "Total number of tables currently detected in this project.")
                    ui.label(str(len(tables))).classes("text-h5 font-extrabold")
                with ui.card().classes("glass-panel p-4 min-w-40"):
                    relations_hdr = ui.label("Relations").classes("text-xs text-slate-500")
                    attach_tooltip(relations_hdr, "Number of table-to-table relationships detected or defined.")
                    ui.label(str(len(relations))).classes("text-h5 font-extrabold")
                with ui.card().classes("glass-panel p-4 min-w-40"):
                    source_hdr = ui.label("Source").classes("text-xs text-slate-500")
                    attach_tooltip(source_hdr, "Project source type, such as CSV ingestion or schema studio.")
                    ui.label(str(project.get("source_type", "UNKNOWN"))).classes("text-h6 font-bold")

            with ui.row().classes("w-full gap-4 flex-wrap"):
                with ui.card().classes("glass-panel p-4 w-full"):
                    names = [t["name"] for t in tables]
                    if names and local_state["selected_table"] not in names:
                        local_state["selected_table"] = names[0]
                    data_profile_hdr = ui.label("Data Profile").classes("text-lg font-bold mb-2").style("color: var(--nexus-brand);")
                    attach_tooltip(data_profile_hdr, "Column-level profile for the selected table.")
                    ui.select(
                        names,
                        value=local_state["selected_table"],
                        on_change=lambda e: (
                            local_state.__setitem__("selected_table", e.value),
                            safe_refresh(project_view),
                        ),
                    ).classes("w-full max-w-sm mb-3")

                    selected = current_table()
                    if selected:
                        ui.aggrid(
                            {
                                "columnDefs": [
                                    {"headerName": "Column", "field": "name", "sortable": True, "filter": True, "minWidth": 160},
                                    {"headerName": "Type", "field": "data_type", "width": 140},
                                ],
                                "rowData": selected["columns"],
                                "defaultColDef": {"resizable": True},
                            }
                        ).classes("h-96 w-full")
                        if should_show_same_table_analytics():
                            if local_state.get("correlation_table_id") != selected.get("id"):
                                asyncio.create_task(refresh_correlations(str(selected.get("id") or "")))
                            ui.separator().classes("my-3 opacity-30")
                            corr_exp = ui.expansion("Correlation (numeric columns)", value=False).classes("w-full")
                            attach_tooltip(corr_exp, "Linear correlation between numeric columns in the selected table.")
                            with corr_exp:
                                if local_state["is_loading_correlation"]:
                                    with ui.row().classes("items-center gap-2 text-slate-500"):
                                        ui.spinner(size="sm")
                                        ui.label("Computing correlations...")
                                elif local_state["correlation_rows"]:
                                    _corr_fmt = ":javascript(params.value == null ? '\u2014' : params.value.toFixed(3))"
                                    ui.aggrid(
                                        {
                                            "columnDefs": [
                                                {"headerName": "Column A", "field": "col_a", "minWidth": 160},
                                                {"headerName": "Column B", "field": "col_b", "minWidth": 160},
                                                {"headerName": "Correlation", "field": "corr", "width": 140, "valueFormatter": _corr_fmt},
                                            ],
                                            "rowData": local_state["correlation_rows"],
                                            "defaultColDef": {"resizable": True},
                                        }
                                    ).classes("h-56 w-full")
                                else:
                                    note = local_state.get("correlation_note") or "Not enough numeric data to compute correlations."
                                    ui.label(note).classes("text-xs text-slate-500 italic")

                            ui.separator().classes("my-3 opacity-30")
                            assoc_exp = ui.expansion("Same-table Associations (categorical)", value=False).classes("w-full")
                            attach_tooltip(assoc_exp, "Association strength between categorical columns in the same table.")
                            with assoc_exp:
                                if local_state["is_loading_correlation"]:
                                    with ui.row().classes("items-center gap-2 text-slate-500"):
                                        ui.spinner(size="sm")
                                        ui.label("Computing associations...")
                                elif local_state["association_rows"]:
                                    _assoc_fmt = ":javascript(params.value == null ? '\u2014' : params.value.toFixed(3))"
                                    ui.aggrid(
                                        {
                                            "columnDefs": [
                                                {"headerName": "Column A", "field": "col_a", "minWidth": 160},
                                                {"headerName": "Column B", "field": "col_b", "minWidth": 160},
                                                {"headerName": "Association", "field": "score", "width": 140, "valueFormatter": _assoc_fmt},
                                                {"headerName": "Metric", "field": "metric", "width": 120},
                                            ],
                                            "rowData": local_state["association_rows"],
                                            "defaultColDef": {"resizable": True},
                                        }
                                    ).classes("h-56 w-full")
                                else:
                                    note = local_state.get("association_note") or "Not enough categorical data to compute associations."
                                    ui.label(note).classes("text-xs text-slate-500 italic")

                            ui.separator().classes("my-3 opacity-30")
                            llm_assoc_exp = ui.expansion("LLM Associations (same table)", value=False).classes("w-full")
                            attach_tooltip(llm_assoc_exp, "LLM-inferred semantic relationships between columns in the selected table.")
                            with llm_assoc_exp:
                                if local_state["is_loading_correlation"]:
                                    with ui.row().classes("items-center gap-2 text-slate-500"):
                                        ui.spinner(size="sm")
                                        ui.label("Inferring associations with LLM...")
                                elif local_state["llm_association_rows"]:
                                    _llm_fmt = ":javascript(params.value == null ? '\u2014' : params.value.toFixed(3))"
                                    ui.aggrid(
                                        {
                                            "columnDefs": [
                                                {"headerName": "Column A", "field": "col_a", "minWidth": 160},
                                                {"headerName": "Column B", "field": "col_b", "minWidth": 160},
                                                {"headerName": "Association", "field": "association", "width": 160},
                                                {"headerName": "Confidence", "field": "confidence", "width": 140, "valueFormatter": _llm_fmt},
                                                {
                                                    "headerName": "Reason",
                                                    "field": "reason",
                                                    "minWidth": 900,
                                                    "tooltipField": "reason",
                                                    "cellStyle": {"white-space": "nowrap"},
                                                },
                                            ],
                                            "rowData": local_state["llm_association_rows"],
                                            "defaultColDef": {"resizable": True},
                                            "alwaysShowHorizontalScroll": True,
                                        }
                                    ).classes("h-64 w-full")
                                else:
                                    note = local_state.get("llm_association_note") or "LLM did not return associations."
                                    ui.label(note).classes("text-xs text-slate-500 italic")

            with ui.row().classes(ACTION_BAR):
                action_button("Back to Setup", icon="arrow_back", on_click=lambda: go_to_page("upload"), variant="outline")
                action_button(
                    "Continue to Modeling",
                    icon="arrow_forward",
                    on_click=lambda: go_to_page("modeling"),
                    variant="primary",
                )

    @ui.refreshable
    def modeling_view() -> None:
        if local_state["page"] != "modeling":
            return
        if not local_state["project_data"]:
            ui.label("No project loaded.").classes("text-slate-500")
            return

        with ui.column().classes("w-full gap-4 fade-up"):
            render_page_header(
                "Modeling",
                "Review each table, then refine types, null handling, privacy, allowed values, and relationships.",
            )
            if local_state["is_inferring_semantics"]:
                with ui.row().classes("items-center gap-2 text-amber-700 bg-amber-50 px-3 py-2 rounded"):
                    ui.spinner(size="sm")
                    ui.label("Running semantic inference with AI...")
            if local_state["is_detecting_pii"]:
                with ui.row().classes("items-center gap-2 text-rose-700 bg-rose-50 px-3 py-2 rounded"):
                    ui.spinner(size="sm")
                    ui.label("Detecting PII columns...")
            if local_state["is_expanding_categories"]:
                with ui.row().classes("items-center gap-2 text-sky-700 bg-sky-50 px-3 py-2 rounded"):
                    ui.spinner(size="sm")
                    ui.label("Expanding categorical values...")

            with ui.expansion("1) Column Configuration", value=True).classes("glass-panel p-2 w-full"):
                ui.label("Refine column semantics, privacy flags, and generation strategy.").classes("text-xs text-slate-500 mb-2")
                with ui.row().classes("w-full items-center justify-between gap-2 mb-3 flex-wrap"):
                    ui.label(
                        f"Tables in project: {len(local_state['project_data']['tables'])}"
                    ).classes("text-sm font-semibold text-slate-600")
                    with ui.row().classes("items-center gap-2"):
                        pii_btn = action_button(
                            "Auto Detect PII",
                            icon="privacy_tip",
                            on_click=detect_pii_with_ai,
                            variant="danger",
                            compact=True,
                        )
                        pii_btn.set_enabled(not local_state["is_detecting_pii"])
                        infer_btn = action_button(
                            "Auto Infer (AI)",
                            icon="auto_awesome",
                            on_click=infer_semantics_with_ai,
                            variant="warning",
                            compact=True,
                        )
                        infer_btn.set_enabled(not local_state["is_inferring_semantics"])
                        action_button("Save Blueprint", icon="save", on_click=save_modeling, variant="success", compact=True)
                        action_button(
                            "Go to Generate",
                            icon="arrow_forward",
                            on_click=lambda: asyncio.create_task(save_modeling_and_open_generate()),
                            variant="primary",
                            compact=True,
                        )

                tables_for_modeling = local_state["project_data"].get("tables", [])
                if tables_for_modeling:
                    with ui.column().classes("w-full gap-3"):
                        for table in tables_for_modeling:
                            render_modeling_table_editor(table)
                else:
                    ui.label("No table selected.").classes("text-sm text-slate-500")

            with ui.expansion("2) Relationship Studio", value=True).classes("glass-panel p-2 w-full"):
                tables = table_names_in_project()
                if len(tables) <= 1:
                    ui.label("Add at least two tables to define relationships.").classes("text-sm text-slate-500 italic")
                else:
                    if local_state["is_inferring_relations"]:
                        with ui.row().classes("items-center gap-2 text-emerald-700 bg-emerald-50 px-3 py-2 rounded mb-2"):
                            ui.spinner(size="sm")
                            ui.label("Inferring relationships with AI...")
                    with ui.row().classes("w-full items-center justify-between gap-2 mb-2 flex-wrap"):
                        with ui.row().classes("items-center gap-2"):
                            infer_btn = action_button(
                                "Infer Relationships (AI)",
                                icon="auto_awesome",
                                on_click=infer_relationships_with_ai,
                                variant="warning",
                                compact=True,
                            )
                            infer_btn.set_enabled(not local_state["is_inferring_relations"])
                            action_button(
                                "Add Relationship",
                                icon="add_link",
                                on_click=add_relation_row,
                                variant="outline",
                                compact=True,
                            )
                        save_rel_btn = action_button(
                            "Save Relationships",
                            icon="save",
                            on_click=save_relationships,
                            variant="success",
                            compact=True,
                        )
                        save_rel_btn.set_enabled(not local_state["is_saving_relations"])

                    if not local_state["editable_relations"]:
                        ui.label("No relationships yet. Use AI inference or add manually.").classes("text-sm text-slate-500")
                    else:
                        for r_idx, rel in enumerate(local_state["editable_relations"]):
                            with ui.card().classes("bg-white/70 border border-slate-200 rounded p-3 mb-2"):
                                with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                                    ui.select(
                                        [""] + tables,
                                        label="From table",
                                        value=rel.get("from_table", ""),
                                        on_change=lambda _, i=r_idx: on_relation_table_change(i),
                                    ).bind_value(rel, "from_table").classes("w-44")
                                    ui.select(
                                        [""] + columns_for_table(rel.get("from_table", "")),
                                        label="From column",
                                    ).bind_value(rel, "from_column").classes("w-44")
                                    ui.icon("east", size="sm").classes("text-slate-400 mb-3")
                                    ui.select(
                                        [""] + tables,
                                        label="To table",
                                        value=rel.get("to_table", ""),
                                        on_change=lambda _, i=r_idx: on_relation_table_change(i),
                                    ).bind_value(rel, "to_table").classes("w-44")
                                    ui.select(
                                        [""] + columns_for_table(rel.get("to_table", "")),
                                        label="To column",
                                    ).bind_value(rel, "to_column").classes("w-44")
                                    ui.select(["1:N", "1:1", "N:1", "N:N"], label="Cardinality").bind_value(
                                        rel, "cardinality"
                                    ).classes("w-28")
                                    ui.checkbox("Optional").bind_value(rel, "is_optional")
                                    rm_rel_btn = ui.button(icon="delete").props("flat dense round color=negative size=sm")
                                    rm_rel_btn.on_click(lambda _, i=r_idx: remove_relation_row(i))

            with ui.expansion("3) Visualize", value=True).classes("glass-panel p-2 w-full"):
                rels_for_viz = [
                    r
                    for r in local_state.get("editable_relations", [])
                    if str(r.get("from_table", "")).strip()
                    and str(r.get("from_column", "")).strip()
                    and str(r.get("to_table", "")).strip()
                    and str(r.get("to_column", "")).strip()
                ]
                if not rels_for_viz:
                    rels_for_viz = local_state["project_data"].get("relations", [])

                table_names = table_names_in_project()
                if not table_names:
                    ui.label("No tables available yet.").classes("text-sm text-slate-500")
                else:
                    node_data = [{"name": t, "symbolSize": 48} for t in table_names]
                    edge_data = []
                    for r in rels_for_viz:
                        from_t = str(r.get("from_table") or "")
                        to_t = str(r.get("to_table") or "")
                        from_c = str(r.get("from_column") or "")
                        to_c = str(r.get("to_column") or "")
                        card = str(r.get("cardinality") or "1:N")
                        if from_t and to_t:
                            edge_data.append(
                                {
                                    "source": to_t,
                                    "target": from_t,
                                    "label": {"show": True, "formatter": f"{to_c} -> {from_c} ({card})"},
                                }
                            )

                    if edge_data:
                        ui.echart(
                            {
                                "tooltip": {"trigger": "item"},
                                "animationDuration": 600,
                                "series": [
                                    {
                                        "type": "graph",
                                        "layout": "force",
                                        "roam": True,
                                        "data": node_data,
                                        "links": edge_data,
                                        "force": {"repulsion": 680, "edgeLength": [160, 230]},
                                        "label": {"show": True, "fontWeight": "bold"},
                                        "lineStyle": {"width": 2.2, "curveness": 0.16, "opacity": 0.85},
                                    }
                                ],
                            }
                        ).classes("w-full h-96")
                    else:
                        ui.label("No relationships defined yet. Save relationships to visualize the model.").classes(
                            "text-sm text-slate-500"
                        )

            with ui.row().classes(ACTION_BAR):
                action_button("Back to Workspace", icon="arrow_back", on_click=lambda: go_to_page("project"), variant="outline")
                clear_btn = action_button(
                    "Clear All Tables",
                    icon="delete_sweep",
                    on_click=lambda: asyncio.create_task(clear_all_model_tables()),
                    variant="danger",
                )
                clear_btn.set_enabled(bool(local_state.get("project_id")))
                action_button(
                    "Go to Generate",
                    icon="arrow_forward",
                    on_click=lambda: asyncio.create_task(save_modeling_and_open_generate()),
                    variant="primary",
                )

    def render_generation_settings_card() -> None:
        sync_generation_table_settings()
        table_names = generation_table_names()
        selected_generation_table = str(
            local_state.get("selected_generation_table") or (table_names[0] if table_names else "")
        ).strip()
        active_generation_settings = generation_settings_for(selected_generation_table)
        with ui.card().classes("glass-panel p-5 lift w-full"):
            ui.label("Generation Settings").classes("text-xl font-extrabold mb-4").style("color: var(--nexus-brand);")
            ui.label("Configure rows and seed per table for the final generation run.").classes("text-sm text-slate-500 mb-1")
            if table_names:
                ui.select(
                    table_names,
                    label="Table",
                    value=selected_generation_table if selected_generation_table in table_names else table_names[0],
                    on_change=lambda e: set_selected_generation_table(str(e.value or "")),
                ).props("outlined").classes("w-full mb-2")
                ui.number(
                    label="Rows to generate",
                    value=int(active_generation_settings.get("num_rows") or 1),
                    on_change=lambda e: set_generation_table_rows(e.value),
                ).props("outlined").classes("w-full mb-2")
                ui.number(
                    label="Seed",
                    value=int(active_generation_settings.get("seed") or 42),
                    on_change=lambda e: set_generation_table_seed(e.value),
                ).props("outlined").classes("w-full mb-2")
                with ui.column().classes("gap-1 mt-2"):
                    ui.label("Current table settings").classes("text-xs font-bold uppercase tracking-wide text-slate-500")
                    for table_name in table_names:
                        cfg = (local_state.get("generation_table_settings") or {}).get(table_name) or {}
                        ui.label(
                            f"> {table_name}: rows={int(cfg.get('num_rows') or 1)}, seed={int(cfg.get('seed') if cfg.get('seed') is not None else 42)}"
                        ).classes("text-xs mono text-slate-600")
            else:
                ui.label("No tables are available yet. Return to Input or Workspace and load a project first.").classes(
                    "text-sm text-slate-500 mb-3"
                )
            ui.radio(["csv", "parquet"], value="csv").bind_value(local_state, "output_format").props("inline")

    @ui.refreshable
    def generate_view() -> None:
        if local_state["page"] != "generate":
            return
        if not local_state["project_data"]:
            ui.label("No project loaded.").classes("text-slate-500")
            return
        flush_pending_download()

        render_page_header(
            "Generate A Sample",
            "Step 1 of 2: generate and download a 5-row sample, then approve it to continue to Output.",
        )
        sample_generated = bool(local_state.get("sample_generated"))
        sample_ready = bool(local_state.get("sample_confirmed"))
        with ui.column().classes("w-full gap-4 fade-up"):
            with ui.card().classes("glass-panel p-5 w-full"):
                ui.label("Step 1: Generate A Sample").classes("text-lg font-bold mb-2").style("color: var(--nexus-brand);")
                ui.label(
                    "Click below to generate a 5-row sample. The file will download automatically."
                ).classes("text-sm text-slate-500 mb-3")
                sample_btn = action_button(
                    "Generate Sample",
                    icon="download",
                    on_click=lambda: asyncio.create_task(start_generation(sample_only=True)),
                    variant="outline",
                )
                sample_btn.set_enabled(local_state["task_status"] != "running")
                if local_state.get("task_status") == "running" and local_state.get("last_generation_kind") == "sample":
                    ui.label("Generating sample...").classes("text-xs text-slate-500 mt-2")
                if sample_generated and not sample_ready:
                    ui.label("Sample downloaded. Approve it to continue to Output.").classes("text-xs text-slate-500 mt-2")
                    action_button(
                        "Approve Sample",
                        icon="check_circle",
                        on_click=approve_sample_preview,
                        variant="success",
                        compact=True,
                    ).classes("mt-2")
                elif sample_ready:
                    ui.label("Sample approved. Continue to Output to generate the full dataset.").classes("text-xs text-emerald-700 mt-2")
                else:
                    ui.label("Generate a sample first to unlock the next step.").classes("text-xs text-slate-500 mt-2")

            if sample_ready:
                render_generation_settings_card()
            else:
                with ui.card().classes("glass-panel p-5 w-full"):
                    ui.label("Generation Settings").classes("text-xl font-extrabold mb-2").style("color: var(--nexus-brand);")
                    ui.label(
                        "Approve the 5-row sample first to unlock full generation settings."
                    ).classes("text-sm text-slate-500")

        with ui.row().classes(f"{ACTION_BAR} mt-4"):
            action_button("Back to Modeling", icon="arrow_back", on_click=lambda: go_to_page("modeling"), variant="outline")
            continue_btn = action_button(
                "Continue to Output",
                icon="arrow_forward",
                on_click=lambda: go_to_page("output"),
                variant="primary",
            )
            continue_btn.set_enabled(sample_ready)

    @ui.refreshable
    def output_view() -> None:
        if local_state["page"] != "output":
            return
        if not local_state["project_data"]:
            ui.label("No project loaded.").classes("text-slate-500")
            return
        flush_pending_download()

        render_page_header(
            "Review Settings And Generate Output",
            "Step 2 of 2: review generation settings, then generate and download the full dataset.",
        )
        overview_rows = current_generation_overview()
        sample_ready = bool(local_state.get("sample_confirmed"))
        table_names = generation_table_names()
        output_fmt = str(local_state.get("output_format") or "csv").upper()
        full_run_visible = str(local_state.get("last_generation_kind") or "") == "full"

        with ui.column().classes("w-full gap-4 fade-up"):
            with ui.card().classes("glass-panel p-6 w-full"):
                ui.label("Generation Summary").classes("text-lg font-bold mb-2").style("color: var(--nexus-brand);")
                ui.label("Review the final run settings below, then start full dataset generation.").classes(
                    "text-sm text-slate-500 mb-3"
                )
                with ui.row().classes("w-full gap-2 flex-wrap mb-2"):
                    for item in overview_rows:
                        ui.label(item).classes("setup-chip")
                    ui.label(f"Output: {output_fmt}").classes("setup-chip")
                with ui.column().classes("gap-1 mt-2"):
                    ui.label("Per-table rows and seed").classes("text-xs font-bold uppercase tracking-wide text-slate-500")
                    for table_name in table_names:
                        cfg = (local_state.get("generation_table_settings") or {}).get(table_name) or {}
                        ui.label(
                            f"> {table_name}: rows={int(cfg.get('num_rows') or 1)}, seed={int(cfg.get('seed') if cfg.get('seed') is not None else 42)}"
                        ).classes("text-xs mono text-slate-600")

            with ui.row().classes(f"{ACTION_BAR} mt-2"):
                action_button("Back to Generate", icon="arrow_back", on_click=lambda: go_to_page("generate"), variant="outline")
                launch_btn = action_button(
                    "Generate Full Dataset",
                    icon="bolt",
                    on_click=lambda: asyncio.create_task(start_generation()),
                    variant="primary",
                )
                launch_btn.set_enabled(local_state["task_status"] != "running" and sample_ready)
                dl_btn = action_button(
                    "Download Output",
                    icon="download",
                    on_click=handle_download_click,
                    variant="success",
                )
                dl_btn.set_enabled(
                    str(local_state.get("last_generation_kind") or "") == "full"
                    and local_state["task_status"] == "done"
                    and bool(local_state["task_file_url"])
                )

            if full_run_visible:
                with ui.card().classes("glass-panel p-6 w-full"):
                    ui.label("Output Run Status").classes("text-lg font-bold mb-2").style("color: var(--nexus-brand);")
                    ui.label("Launch full generation after sample approval.").classes("text-sm text-slate-500 mb-3")
                    with ui.row().classes("w-full gap-2 flex-wrap mb-3"):
                        for item in overview_rows:
                            ui.label(item).classes("setup-chip")
                    ui.linear_progress(value=0.0).bind_value_from(
                        local_state,
                        "task_progress",
                        lambda v: max(0.0, min(1.0, float(v or 0) / 100.0)),
                    ).classes("mt-1 h-4 rounded-full")
                    with ui.row().classes("w-full mt-3 items-center justify-between flex-wrap gap-2"):
                        ui.label().bind_text_from(
                            local_state, "task_progress", backward=lambda v: f"{int(v)}% complete"
                        ).classes("text-sm font-bold text-slate-600")
                        ui.label().bind_text_from(
                            local_state, "task_status", backward=lambda v: str(v).upper()
                        ).classes("text-sm font-extrabold px-3 py-1 rounded bg-blue-50").style("color: var(--nexus-brand);")
                    if local_state["task_status"] != "idle":
                        with ui.expansion(f"Run Logs ({len(local_state['task_logs'])} events)", value=False).classes("w-full mt-4"):
                            with ui.card().classes("w-full p-4 bg-slate-950 text-emerald-300 rounded-xl border border-slate-800"):
                                with ui.scroll_area().classes("h-72 w-full pr-2"):
                                    for line in local_state["task_logs"]:
                                        ui.label(f"> {line}").classes("text-sm mono mb-1")

    @ui.refreshable
    def admin_view() -> None:
        if local_state["page"] != "admin":
            return
        if not is_admin_user():
            ui.label("Admin access required.").classes("text-slate-500")
            return

        users = list(local_state.get("admin_users") or [])
        search_text = str(local_state.get("admin_search") or "").strip().lower()
        visible_users = [user for user in users if not search_text or search_text in str(user.get("username") or "").lower()]
        total_users = len(users)
        active_users = sum(1 for user in users if user.get("is_active"))
        admin_users = sum(1 for user in users if str(user.get("role") or "") in {"admin", "super_admin"})
        super_admin_users = sum(1 for user in users if str(user.get("role") or "") == "super_admin")

        def render_stat_card(label: str, value: Any, accent: str) -> None:
            with ui.card().classes("glass-panel p-5 min-w-[180px] flex-1"):
                ui.label(label).classes("text-xs font-bold uppercase tracking-wide text-slate-500")
                ui.label(str(value)).classes("text-h4 font-extrabold").style(f"color: {accent};")

        def format_activity_item(item: Dict[str, Any]) -> str:
            username = str(item.get("username") or "user")
            action = str(item.get("action") or "").replace("_", " ").strip() or "activity"
            project_id = str(item.get("project_id") or "").strip()
            table_id = str(item.get("table_id") or "").strip()
            details = str(item.get("details") or "").strip()
            created_at = format_timestamp(item.get("created_at"))

            text = f"{created_at} | {username} | {action}"
            if project_id:
                text += f" | project {project_id[:8]}"
            if table_id:
                text += f" | table {table_id[:8]}"
            if details:
                text += f" | {details}"
            return text

        def activity_table_rows(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
            rows: List[Dict[str, str]] = []
            for item in items or []:
                raw_details = item.get("details")
                total_tokens = ""
                details_text = str(raw_details or "").strip()
                if isinstance(raw_details, str) and raw_details.strip().startswith("{"):
                    try:
                        parsed_details = json.loads(raw_details)
                    except Exception:
                        parsed_details = None
                    if isinstance(parsed_details, dict):
                        token_value = parsed_details.pop("total_tokens", None)
                        if token_value not in (None, ""):
                            total_tokens = str(token_value)
                        details_text = json.dumps(parsed_details, ensure_ascii=True, sort_keys=True) if parsed_details else ""
                rows.append(
                    {
                        "timestamp": format_timestamp(item.get("created_at")),
                        "action": str(item.get("action") or "").replace("_", " ").strip() or "activity",
                        "total_tokens": total_tokens,
                        "details": details_text,
                    }
                )
            return rows

        def open_reset_password_dialog(username: str) -> None:
            with ui.dialog() as dialog, ui.card().classes("glass-panel p-6 w-[420px] max-w-full"):
                ui.label(f"Reset Password: {username}").classes("text-lg font-bold").style("color: var(--nexus-brand);")
                new_password = ui.input("New Password", password=True, password_toggle_button=True).props("outlined")
                new_password.classes("w-full")
                confirm_password = ui.input("Confirm Password", password=True, password_toggle_button=True).props("outlined")
                confirm_password.classes("w-full")
                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    action_button("Cancel", icon="close", on_click=dialog.close, variant="outline", compact=True)
                    action_button(
                        "Reset Password",
                        icon="key",
                        on_click=lambda: asyncio.create_task(
                            reset_admin_user_password(username, new_password.value, confirm_password.value, dialog)
                        ),
                        variant="warning",
                        compact=True,
                    )
            dialog.open()

        def open_role_dialog(username: str, current_role: str) -> None:
            with ui.dialog() as dialog, ui.card().classes("glass-panel p-6 w-[420px] max-w-full"):
                ui.label(f"Change Role: {username}").classes("text-lg font-bold").style("color: var(--nexus-brand);")
                role_select = ui.select(assignable_roles(), value=current_role, label="Role").props("outlined")
                role_select.classes("w-full")
                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    action_button("Cancel", icon="close", on_click=dialog.close, variant="outline", compact=True)
                    action_button(
                        "Save Role",
                        icon="manage_accounts",
                        on_click=lambda: asyncio.create_task(update_admin_user_role(username, role_select.value, dialog)),
                        variant="primary",
                        compact=True,
                    )
            dialog.open()

        def open_delete_dialog(username: str) -> None:
            with ui.dialog() as dialog, ui.card().classes("glass-panel p-6 w-[440px] max-w-full"):
                ui.label(f"Delete {username}?").classes("text-lg font-bold text-red-600")
                ui.label("This permanently removes the account. Deactivation is safer if the user may need access later.").classes(
                    "text-sm text-slate-600"
                )
                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    action_button("Cancel", icon="close", on_click=dialog.close, variant="outline", compact=True)
                    action_button(
                        "Delete User",
                        icon="delete",
                        on_click=lambda: asyncio.create_task(delete_admin_user(username, dialog)),
                        variant="danger",
                        compact=True,
                    )
            dialog.open()

        def open_user_logs_dialog(username: str) -> None:
            logs_state: Dict[str, Any] = {"loading": True, "items": [], "error": ""}

            @ui.refreshable
            def render_logs() -> None:
                if logs_state["loading"]:
                    with ui.row().classes("items-center gap-2 text-slate-500"):
                        ui.spinner(size="sm")
                        ui.label("Loading recent activity...")
                    return
                if logs_state["error"]:
                    ui.label(str(logs_state["error"])).classes("text-sm text-red-500")
                    return
                if not logs_state["items"]:
                    ui.label("No activity recorded for this user yet.").classes("text-sm text-slate-500")
                    return
                ui.aggrid(
                    {
                        "columnDefs": [
                            {"headerName": "Time", "field": "timestamp", "minWidth": 170, "sortable": True},
                            {"headerName": "Action", "field": "action", "minWidth": 170, "sortable": True},
                            {"headerName": "Total Tokens", "field": "total_tokens", "minWidth": 130, "sortable": True},
                            {
                                "headerName": "Details",
                                "field": "details",
                                "minWidth": 430,
                                "wrapText": True,
                                "autoHeight": True,
                                "cellStyle": {"white-space": "normal", "line-height": "1.4"},
                            },
                        ],
                        "rowData": activity_table_rows(logs_state["items"]),
                        "defaultColDef": {"resizable": True},
                    }
                ).classes("w-full h-72")

            async def load_user_logs() -> None:
                try:
                    async with api_client(timeout=20.0) as client_api:
                        resp = await client_api.get(f"{BACKEND_URL}/auth/admin/activity/{username}", params={"limit": 50})
                    payload = await parse_response(resp)
                    logs_state["items"] = list(payload.get("items") or [])
                    logs_state["error"] = ""
                except Exception as ex:
                    logs_state["error"] = f"Failed to load logs: {ex}"
                    logs_state["items"] = []
                finally:
                    logs_state["loading"] = False
                    render_logs.refresh()

            with ui.dialog() as dialog, ui.card().classes("glass-panel p-6 w-[900px] max-w-full"):
                ui.label(f"User Logs: {username}").classes("text-xl font-bold mb-1").style("color: var(--nexus-brand);")
                ui.label("Recent tracked actions for this user.").classes("text-sm text-slate-500 mb-3")
                render_logs()
                with ui.row().classes("w-full justify-end mt-4"):
                    action_button("Close", icon="close", on_click=dialog.close, variant="outline", compact=True)
            asyncio.create_task(load_user_logs())
            dialog.open()

        def open_create_user_dialog() -> None:
            clear_admin_create_form(refresh=False)
            with ui.dialog() as dialog, ui.card().classes("glass-panel p-6 w-[520px] max-w-full"):
                ui.label("Create User").classes("text-xl font-bold mb-1").style("color: var(--nexus-brand);")
                ui.label("Add a new account, choose the role, and decide whether it should be active immediately.").classes(
                    "text-sm text-slate-500 mb-3"
                )
                ui.label("Password rule: at least 6 characters.").classes("text-xs font-medium text-amber-700 mb-2")
                username_input = ui.input("Username").bind_value(local_state, "admin_create_username").props("outlined")
                username_input.classes("w-full")
                password_input = ui.input("Password", password=True, password_toggle_button=True).bind_value(
                    local_state, "admin_create_password"
                ).props("outlined")
                password_input.classes("w-full")
                confirm_input = ui.input("Confirm Password", password=True, password_toggle_button=True).bind_value(
                    local_state, "admin_create_confirm_password"
                ).props("outlined")
                confirm_input.classes("w-full")
                role_select = ui.select(assignable_roles(), label="Role").bind_value(local_state, "admin_create_role").props("outlined")
                role_select.classes("w-full")
                ui.switch("Active").bind_value(local_state, "admin_create_active").classes("mt-2")
                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    action_button("Cancel", icon="close", on_click=dialog.close, variant="outline", compact=True)
                    action_button(
                        "Create User",
                        icon="person_add",
                        on_click=lambda: asyncio.create_task(create_admin_user(dialog)),
                        variant="primary",
                        compact=True,
                    )
            dialog.open()

        render_page_header(
            "Manage Users",
            "Review and manage users from one place, and add a new user only when needed from the top-right action.",
        )

        with ui.column().classes("w-full gap-6 fade-up"):
            with ui.row().classes("w-full gap-4 flex-wrap"):
                render_stat_card("Total Users", total_users, "var(--nexus-brand)")
                render_stat_card("Active Users", active_users, "#059669")
                render_stat_card("Admins", admin_users, "#d97706")
                render_stat_card("Super Admins", super_admin_users, "#7c3aed")

            with ui.row().classes("w-full items-start"):
                with ui.card().classes("glass-panel admin-workflow-card p-6 w-full"):
                    with ui.row().classes("w-full items-center justify-between gap-3 mb-2"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label("Users").classes("admin-step-chip")
                            ui.label("Review And Manage Users").classes("text-lg font-bold").style("color: var(--nexus-brand);")
                        with ui.row().classes("items-center gap-2"):
                            action_button(
                                "Refresh",
                                icon="refresh",
                                on_click=lambda: asyncio.create_task(load_admin_data()),
                                variant="outline",
                                compact=True,
                            )
                            add_btn = ui.button(icon="person_add", on_click=open_create_user_dialog).props("round unelevated")
                            add_btn.classes("assistant-choice")
                            attach_tooltip(add_btn, "Add user")
                    ui.label("Search for a user, review their status, then use the actions under that user card.").classes(
                        "text-sm text-slate-500 mb-4"
                    )
                    search_input = ui.input("Search username...").bind_value(local_state, "admin_search").props("outlined clearable")
                    search_input.classes("w-full max-w-sm")
                    search_input.on("keydown.enter", lambda _: safe_refresh(admin_view))
                    search_input.on("blur", lambda _: safe_refresh(admin_view))
                    with ui.column().classes("w-full mt-4"):
                        if local_state.get("admin_loading") and not users:
                            ui.label("Loading users...").classes("text-sm text-slate-500 py-3")
                        elif not visible_users:
                            ui.label("No users match the current search.").classes("text-sm text-slate-500 py-3")
                        for user in visible_users:
                            username = str(user.get("username") or "")
                            role = str(user.get("role") or "user")
                            is_active = bool(user.get("is_active"))
                            is_self = username.lower() == str(auth_user().get("username") or "").lower()
                            can_manage_target = is_super_admin_user() or role != "super_admin"
                            role_badge = "super admin" if role == "super_admin" else ("admin" if role == "admin" else "user")
                            title = f"{username} - {role_badge} {'- active' if is_active else '- inactive'}"
                            with ui.expansion(title, value=False).classes("admin-user-card w-full p-2 mb-3"):
                                with ui.column().classes("p-3 gap-3"):
                                    with ui.row().classes("w-full gap-2 flex-wrap"):
                                        ui.label(role.replace("_", " ").title()).classes("setup-chip")
                                        ui.label("Active" if is_active else "Inactive").classes(
                                            "setup-chip"
                                        ).style(
                                            "background: #ecfdf5; color: #047857; border-color: #a7f3d0;"
                                            if is_active
                                            else "background: #fff1f2; color: #be123c; border-color: #fecdd3;"
                                        )
                                        if is_self:
                                            ui.label("Current session").classes("setup-chip")

                                    ui.label("Use the actions below to update this account.").classes("text-sm text-slate-500")

                                    with ui.row().classes("admin-user-meta w-full mt-1"):
                                        with ui.column().classes("gap-1"):
                                            ui.label("Created On").classes("admin-meta-label")
                                            ui.label(format_timestamp(user.get("created_at"))).classes("admin-meta-value")
                                        with ui.column().classes("gap-1"):
                                            ui.label("Last Login").classes("admin-meta-label")
                                            ui.label(format_timestamp(user.get("last_login_at"))).classes("admin-meta-value")

                                    with ui.row().classes("admin-actions w-full gap-2 flex-wrap justify-start mt-2 pt-4"):
                                        action_button(
                                            "Reset Password",
                                            icon="key",
                                            on_click=lambda name=username: open_reset_password_dialog(name),
                                            variant="outline",
                                            compact=True,
                                        ).set_enabled(can_manage_target)
                                        action_button(
                                            "Change Role",
                                            icon="manage_accounts",
                                            on_click=lambda name=username, current_role=role: open_role_dialog(name, current_role),
                                            variant="outline",
                                            compact=True,
                                        ).set_enabled(not is_self and can_manage_target)
                                        action_button(
                                            "Deactivate" if is_active else "Activate",
                                            icon="toggle_off" if is_active else "toggle_on",
                                            on_click=lambda name=username, next_status=not is_active: asyncio.create_task(
                                                update_admin_user_status(name, next_status)
                                            ),
                                            variant="warning" if is_active else "success",
                                            compact=True,
                                        ).set_enabled(not is_self and can_manage_target)
                                        action_button(
                                            "Delete",
                                            icon="delete",
                                            on_click=lambda name=username: open_delete_dialog(name),
                                            variant="danger",
                                            compact=True,
                                        ).set_enabled(not is_self and can_manage_target)
                                        action_button(
                                            "Logs",
                                            icon="history",
                                            on_click=lambda name=username: open_user_logs_dialog(name),
                                            variant="outline",
                                            compact=True,
                                        )

            with ui.expansion("Recent Admin Actions", value=False).classes("glass-panel p-2 w-full"):
                with ui.column().classes("p-4 gap-2"):
                    if not local_state.get("admin_audit"):
                        ui.label("No admin actions recorded yet.").classes("text-sm text-slate-500")
                    else:
                        for item in local_state.get("admin_audit") or []:
                            actor = str(item.get("actor_username") or "admin")
                            action = str(item.get("action") or "").replace("_", " ")
                            target = str(item.get("target_username") or "").strip()
                            details = str(item.get("details") or "").strip()
                            text = f"{actor} {action}"
                            if target:
                                text += f" for {target}"
                            if details:
                                text += f" ({details})"
                            ui.label(f"- {text}").classes("text-sm text-slate-600")

    @ui.refreshable
    def assistant_widget() -> None:
        # Keep chatbot icon/widget only on Setup page
        if str(local_state.get("page") or "") != "upload":
            if local_state.get("assistant_open"):
                local_state["assistant_open"] = False
            if local_state.get("assistant_fullscreen"):
                local_state["assistant_fullscreen"] = False
            return

        # If a sample/full file download was queued from a background task,
        # flush it here as well so chatbot-driven flows on Setup can auto-download.
        flush_pending_download()

        def render_assistant_shell() -> None:
            shell_classes = "assistant-shell p-0 overflow-hidden"
            if local_state.get("assistant_fullscreen"):
                shell_classes += " fullscreen"
            with ui.card().classes(shell_classes):
                with ui.column().classes("assistant-header w-full gap-2 p-4"):
                    with ui.row().classes("w-full items-start justify-between gap-3"):
                        with ui.column().classes("gap-1"):
                            ui.label("Data Assistant").classes("text-h6 font-extrabold")
                            ui.label(f"Step {assistant_page_title()}").classes("text-xs text-white/80")
                        with ui.row().classes("items-center gap-1"):
                            ui.button(icon="add_comment", on_click=start_new_assistant_chat).props("flat round dense color=white")
                            ui.button(
                                icon="fullscreen_exit" if local_state.get("assistant_fullscreen") else "fullscreen",
                                on_click=toggle_assistant_fullscreen,
                            ).props("flat round dense color=white")
                            ui.button(icon="close", on_click=toggle_assistant).props("flat round dense color=white")
                    with ui.row().classes("gap-2 flex-wrap"):
                        ui.label(f"Page: {assistant_current_page()}").classes("assistant-chip")
                        ui.label(
                            f"Project: {'Ready' if local_state.get('project_id') else 'Not ready'}"
                        ).classes("assistant-chip")
                        ui.label(
                            f"Mode: {(local_state.get('setup_mode') or 'none').upper()}"
                        ).classes("assistant-chip")
                        if local_state.get("assistant_mode_active"):
                            ui.label("Chat Flow Active").classes("assistant-chip")
                with ui.scroll_area().classes("assistant-scroll w-full"):
                    with ui.column().classes("w-full gap-3 p-4"):
                        for message in assistant_messages():
                            role = message.get("role", "assistant")
                            with ui.column().classes(f"w-full {'items-end' if role == 'user' else 'items-start'}"):
                                ui.label(message.get("text", "")).classes(
                                    f"assistant-bubble {role}"
                                )
                        if local_state.get("assistant_busy"):
                            with ui.column().classes("w-full items-start"):
                                ui.label("Thinking through that request...").classes("assistant-bubble assistant")
                        render_assistant_stage_panel()
                        choice_items = assistant_choices()
                        if choice_items:
                            with ui.column().classes("w-full items-start gap-2 pt-1"):
                                ui.label("Choose the next step below or continue typing.").classes("text-xs text-slate-500")
                                with ui.row().classes("w-full gap-2 flex-wrap"):
                                    for choice in choice_items:
                                        btn = ui.button(
                                            str(choice.get("label") or "Option"),
                                            on_click=lambda _, item=choice: handle_assistant_choice(item),
                                        ).props("flat no-caps")
                                        btn.classes("assistant-choice px-4 py-2")
                        ui.element("div").classes("assistant-scroll-anchor h-px w-full")
                with ui.column().classes("w-full gap-2 p-4 pt-3"):
                    prompt = ui.input(
                        placeholder="Ask a question or tell me what to do next...",
                    ).bind_value(local_state, "assistant_input").props("outlined dense")
                    prompt.classes("w-full")
                    prompt.on("keydown.enter", lambda _: asyncio.create_task(send_assistant_message()))
                    prompt.set_enabled(not local_state.get("assistant_busy"))
                    with ui.row().classes("w-full items-center justify-between gap-2"):
                        send_btn = action_button(
                            "Send",
                            icon="send",
                            on_click=lambda: asyncio.create_task(send_assistant_message()),
                            variant="primary",
                            compact=True,
                        )
                        send_btn.set_enabled(not local_state.get("assistant_busy"))

        if local_state.get("assistant_open") and local_state.get("assistant_fullscreen"):
            with ui.element("div").classes("fixed inset-0 z-[1100] flex items-start justify-start p-4 pointer-events-none"):
                with ui.element("div").classes("w-full h-full pointer-events-auto"):
                    render_assistant_shell()
        else:
            with ui.page_sticky(position="bottom-right", x_offset=24, y_offset=24):
                with ui.column().classes("items-end gap-3"):
                    if local_state.get("assistant_open"):
                        render_assistant_shell()
                    fab = ui.button(icon="smart_toy", on_click=toggle_assistant).props("round unelevated")
                    fab.classes("assistant-fab")
                    attach_tooltip(fab, "Hi 👋 How can I help you today?")


    if local_state.get("auth_token"):
        if local_state["project_id"] and local_state["page"] in {"project", "modeling", "generate", "output"}:
            asyncio.create_task(
                load_project(
                    refresh_plan=local_state["page"] in {"generate", "output"},
                    refresh_summary=local_state["page"] == "project",
                )
            )

        nav_bar()
        with ui.column().classes("w-full max-w-7xl mx-auto p-4 gap-2"):
            upload_view()
            input_view()
            project_view()
            modeling_view()
            generate_view()
            output_view()
            admin_view()
        assistant_widget()
    else:
        with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-2"):
            login_view()


ui.run(
    title="DataCosmos",
    host=os.getenv("NICEGUI_HOST", "0.0.0.0"),
    port=int(os.getenv("NICEGUI_PORT", "8181")),
    reload=False,
    storage_secret=os.getenv("NICEGUI_STORAGE_SECRET", "nexus-local-dev-secret-change-me"),
)
