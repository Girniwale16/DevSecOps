import hashlib
import json
import os
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Request

from app.engine.metadata import get_db_connection

AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "[REDACTED_GENERIC_PASSWORD_1]")
SUPER_ADMIN_USERNAME = os.getenv("SUPER_ADMIN_USERNAME", "superadmin")
SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "[REDACTED_GENERIC_PASSWORD_2]")

PUBLIC_PATHS = {
    "/",
    "/auth/login",
    "/openapi.json",
    "/docs",
    "/redoc",
}

SUPER_ADMIN_ROLE = "super_admin"
ADMIN_ROLE = "admin"
USER_ROLE = "user"

PRIVILEGED_ROLES = {SUPER_ADMIN_ROLE, ADMIN_ROLE}
ADMIN_ROLES = set(PRIVILEGED_ROLES)
USER_ROLES = {SUPER_ADMIN_ROLE, ADMIN_ROLE, USER_ROLE}

sessions: Dict[str, Dict[str, Any]] = {}
router = APIRouter(prefix="/auth", tags=["auth"])


def is_public_path(path: str) -> bool:
    if not path:
        return False
    if path in PUBLIC_PATHS:
        return True
    return path.startswith("/_nicegui")


def extract_bearer_token(raw_header: Optional[str]) -> Optional[str]:
    header = str(raw_header or "").strip()
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    return token or None


def _normalize_role(value: Any) -> str:
    role = str(value or USER_ROLE).strip().lower()
    if role not in USER_ROLES:
        raise HTTPException(status_code=400, detail="Role must be 'super_admin', 'admin', or 'user'")
    return role


def _normalize_username(value: Any) -> str:
    username = str(value or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if len(username) > 64:
        raise HTTPException(status_code=400, detail="Username must be 64 characters or fewer")
    return username


def _normalize_password(value: Any) -> str:
    password = str(value or "")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    return password


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    clean_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        clean_salt.encode("utf-8"),
        200_000,
    )
    return clean_salt, digest.hex()


def _verify_password(password: str, salt: str, password_hash: str) -> bool:
    _, computed_hash = _hash_password(password, salt)
    return secrets.compare_digest(computed_hash, str(password_hash or ""))


def _has_super_admin_rights(user: Optional[Dict[str, Any]]) -> bool:
    return str((user or {}).get("role") or "").strip().lower() == SUPER_ADMIN_ROLE


def _assert_can_manage_target_user(actor_user: Dict[str, Any], target_role: str, action_label: str) -> None:
    if target_role == SUPER_ADMIN_ROLE and not _has_super_admin_rights(actor_user):
        raise HTTPException(status_code=403, detail=f"Only super admin can {action_label} super admin accounts")


def _assert_can_assign_role(actor_user: Dict[str, Any], requested_role: str) -> None:
    if requested_role == SUPER_ADMIN_ROLE and not _has_super_admin_rights(actor_user):
        raise HTTPException(status_code=403, detail="Only super admin can assign super admin role")


def _token_reference(token: Optional[str]) -> Optional[str]:
    clean = str(token or "").strip()
    if not clean:
        return None
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    return digest[:10]


def init_auth_storage() -> None:
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_users (
            username VARCHAR PRIMARY KEY,
            password_salt VARCHAR NOT NULL,
            password_hash VARCHAR NOT NULL,
            role VARCHAR NOT NULL DEFAULT 'user',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP,
            password_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    try:
        conn.execute("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS last_logout_at TIMESTAMP")
    except Exception:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_logs (
            id VARCHAR PRIMARY KEY,
            actor_username VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            target_username VARCHAR,
            details VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_activity_logs (
            id VARCHAR PRIMARY KEY,
            username VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            project_id VARCHAR,
            table_id VARCHAR,
            details VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    existing_super_admin = conn.execute(
        "SELECT username FROM app_users WHERE lower(username) = lower(?) LIMIT 1",
        (SUPER_ADMIN_USERNAME,),
    ).fetchone()
    if not existing_super_admin:
        salt, password_hash = _hash_password(SUPER_ADMIN_PASSWORD)
        conn.execute(
            """
            INSERT INTO app_users (
                username, password_salt, password_hash, role, is_active, created_at, updated_at, password_changed_at
            )
            VALUES (?, ?, ?, ?, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (SUPER_ADMIN_USERNAME, salt, password_hash, SUPER_ADMIN_ROLE),
        )

    existing_admin = conn.execute(
        "SELECT username FROM app_users WHERE lower(username) = lower(?) LIMIT 1",
        (AUTH_USERNAME,),
    ).fetchone()
    if not existing_admin:
        salt, password_hash = _hash_password(AUTH_PASSWORD)
        conn.execute(
            """
            INSERT INTO app_users (
                username, password_salt, password_hash, role, is_active, created_at, updated_at, password_changed_at
            )
            VALUES (?, ?, ?, ?, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (AUTH_USERNAME, salt, password_hash, ADMIN_ROLE),
        )
    conn.close()


def _row_to_user(row: Any) -> Dict[str, Any]:
    return {
        "username": row[0],
        "role": row[1],
        "is_active": bool(row[2]),
        "created_at": row[3].isoformat() if row[3] else None,
        "last_login_at": row[4].isoformat() if row[4] else None,
    }


def _public_user_payload(username: str) -> Dict[str, Any]:
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT username, role, is_active, created_at, last_login_at
        FROM app_users
        WHERE lower(username) = lower(?)
        LIMIT 1
        """,
        (username,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    user = _row_to_user(row)
    user["display_name"] = user["username"]
    return user


def _count_active_admins(conn: Any) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM app_users WHERE role IN (?, ?) AND is_active = TRUE",
        (SUPER_ADMIN_ROLE, ADMIN_ROLE),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _count_active_super_admins(conn: Any) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM app_users WHERE role = ? AND is_active = TRUE",
        (SUPER_ADMIN_ROLE,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _write_audit_log(actor_username: str, action: str, target_username: Optional[str], details: Optional[str] = None) -> None:
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO admin_audit_logs (id, actor_username, action, target_username, details, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (str(uuid.uuid4()), actor_username, action, target_username, details),
    )
    conn.close()


def _stringify_details(details: Any) -> Optional[str]:
    if details is None:
        return None
    if isinstance(details, str):
        clean = details.strip()
        return clean or None
    try:
        return json.dumps(details, ensure_ascii=True, sort_keys=True)
    except Exception:
        clean = str(details).strip()
        return clean or None


def _details_with_token_ref(details: Any, token: Optional[str]) -> Any:
    token_ref = _token_reference(token)
    if not token_ref:
        return details
    if details is None:
        return {"token_ref": token_ref}
    if isinstance(details, dict):
        merged = dict(details)
        merged.setdefault("token_ref", token_ref)
        return merged
    clean = str(details).strip()
    if not clean:
        return {"token_ref": token_ref}
    return {"message": clean, "token_ref": token_ref}


def write_user_activity(
    username: str,
    action: str,
    *,
    project_id: Optional[str] = None,
    table_id: Optional[str] = None,
    details: Any = None,
) -> None:
    clean_username = str(username or "").strip()
    clean_action = str(action or "").strip()
    if not clean_username or not clean_action:
        return
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO user_activity_logs (id, username, action, project_id, table_id, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            str(uuid.uuid4()),
            clean_username,
            clean_action,
            str(project_id).strip() if project_id else None,
            str(table_id).strip() if table_id else None,
            _stringify_details(details),
        ),
    )
    conn.close()


def log_request_activity(
    request: Request,
    action: str,
    *,
    project_id: Optional[str] = None,
    table_id: Optional[str] = None,
    details: Any = None,
) -> None:
    user = getattr(request.state, "user", None) or current_user_from_request(request)
    username = str((user or {}).get("username") or "").strip()
    if not username:
        return
    write_user_activity(
        username,
        action,
        project_id=project_id,
        table_id=table_id,
        details=details,
    )


def get_session(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    session = sessions.get(token)
    if not session:
        return None
    username = str(session.get("username") or "").strip()
    if not username:
        sessions.pop(token, None)
        return None
    try:
        user = _public_user_payload(username)
    except HTTPException:
        sessions.pop(token, None)
        return None
    if not user.get("is_active"):
        sessions.pop(token, None)
        return None
    user["login_at"] = session.get("login_at")
    return user


def current_user_from_request(request: Request) -> Optional[Dict[str, Any]]:
    auth_header = request.headers.get("Authorization")
    token = extract_bearer_token(auth_header)
    return get_session(token)


def require_admin(request: Request) -> Dict[str, Any]:
    user = current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("role") not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _recent_audit_rows(limit: int = 12) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT actor_username, action, target_username, details, created_at
        FROM admin_audit_logs
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 50)),),
    ).fetchall()
    conn.close()
    return [
        {
            "actor_username": row[0],
            "action": row[1],
            "target_username": row[2],
            "details": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
        }
        for row in rows
    ]


def _recent_activity_rows(limit: int = 20, username: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    clean_username = str(username or "").strip()
    if clean_username:
        rows = conn.execute(
            """
            SELECT username, action, project_id, table_id, details, created_at
            FROM user_activity_logs
            WHERE lower(username) = lower(?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (clean_username, max(1, min(int(limit), 100))),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT username, action, project_id, table_id, details, created_at
            FROM user_activity_logs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    conn.close()
    return [
        {
            "username": row[0],
            "action": row[1],
            "project_id": row[2],
            "table_id": row[3],
            "details": row[4],
            "created_at": row[5].isoformat() if row[5] else None,
        }
        for row in rows
    ]


@router.post("/login")
async def login(payload: Dict[str, Any] = Body(default={})):
    username = _normalize_username(payload.get("username"))
    password = str(payload.get("password") or "")

    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT username, password_salt, password_hash, role, is_active, created_at, last_login_at
        FROM app_users
        WHERE lower(username) = lower(?)
        LIMIT 1
        """,
        (username,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    db_username, password_salt, password_hash, role, is_active, created_at, last_login_at = row
    if not is_active or not _verify_password(password, password_salt, password_hash):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    login_epoch = int(time.time())
    conn.execute(
        "UPDATE app_users SET last_login_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE lower(username) = lower(?)",
        (db_username,),
    )
    conn.close()

    token = secrets.token_urlsafe(32)
    sessions[token] = {"username": db_username, "login_at": login_epoch}
    user = {
        "username": db_username,
        "display_name": db_username,
        "role": role,
        "is_active": bool(is_active),
        "created_at": created_at.isoformat() if created_at else None,
        "last_login_at": login_epoch,
        "login_at": login_epoch,
    }
    write_user_activity(db_username, "login", details=_details_with_token_ref({"role": role}, token))
    return {"token": token, "user": user}


@router.get("/me")
async def me(request: Request):
    user = current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"user": user}


@router.post("/logout")
async def logout(authorization: Optional[str] = Header(default=None)):
    token = extract_bearer_token(authorization)
    if token:
        session = sessions.pop(token, None)
        username = str((session or {}).get("username") or "").strip()
        if username:
            conn = get_db_connection()
            conn.execute(
                "UPDATE app_users SET last_logout_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE lower(username) = lower(?)",
                (username,),
            )
            conn.close()
            write_user_activity(username, "logout", details=_details_with_token_ref(None, token))
    return {"ok": True}


@router.get("/admin/users")
async def admin_list_users(request: Request):
    require_admin(request)
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT username, role, is_active, created_at, last_login_at
        FROM app_users
        ORDER BY lower(username)
        """
    ).fetchall()
    conn.close()
    return {"users": [_row_to_user(row) for row in rows]}


@router.post("/admin/users")
async def admin_create_user(request: Request, payload: Dict[str, Any] = Body(default={})):
    admin_user = require_admin(request)
    username = _normalize_username(payload.get("username"))
    password = _normalize_password(payload.get("password"))
    role = _normalize_role(payload.get("role"))
    _assert_can_assign_role(admin_user, role)
    is_active = bool(payload.get("is_active", True))

    conn = get_db_connection()
    existing = conn.execute(
        "SELECT username FROM app_users WHERE lower(username) = lower(?) LIMIT 1",
        (username,),
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail="Username already exists")

    salt, password_hash = _hash_password(password)
    conn.execute(
        """
        INSERT INTO app_users (
            username, password_salt, password_hash, role, is_active, created_at, updated_at, password_changed_at
        )
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (username, salt, password_hash, role, is_active),
    )
    conn.close()
    _write_audit_log(admin_user["username"], "create_user", username, f"role={role};active={is_active}")
    return {"ok": True, "user": _public_user_payload(username)}


@router.post("/admin/users/{username}/reset-password")
async def admin_reset_password(username: str, request: Request, payload: Dict[str, Any] = Body(default={})):
    admin_user = require_admin(request)
    clean_username = _normalize_username(username)
    new_password = _normalize_password(payload.get("password"))
    salt, password_hash = _hash_password(new_password)

    conn = get_db_connection()
    existing = conn.execute(
        "SELECT username, role FROM app_users WHERE lower(username) = lower(?) LIMIT 1",
        (clean_username,),
    ).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")
    try:
        _assert_can_manage_target_user(admin_user, str(existing[1] or "").strip().lower(), "reset passwords for")
    except HTTPException:
        conn.close()
        raise

    conn.execute(
        """
        UPDATE app_users
        SET password_salt = ?, password_hash = ?, updated_at = CURRENT_TIMESTAMP, password_changed_at = CURRENT_TIMESTAMP
        WHERE lower(username) = lower(?)
        """,
        (salt, password_hash, clean_username),
    )
    conn.close()
    _write_audit_log(admin_user["username"], "reset_password", clean_username)
    return {"ok": True}


@router.post("/admin/users/{username}/role")
async def admin_change_role(username: str, request: Request, payload: Dict[str, Any] = Body(default={})):
    admin_user = require_admin(request)
    clean_username = _normalize_username(username)
    new_role = _normalize_role(payload.get("role"))
    _assert_can_assign_role(admin_user, new_role)

    conn = get_db_connection()
    row = conn.execute(
        "SELECT username, role, is_active FROM app_users WHERE lower(username) = lower(?) LIMIT 1",
        (clean_username,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    db_username, current_role, is_active = row
    current_role = str(current_role or "").strip().lower()
    try:
        _assert_can_manage_target_user(admin_user, current_role, "change roles for")
    except HTTPException:
        conn.close()
        raise

    if db_username.lower() == admin_user["username"].lower() and current_role in PRIVILEGED_ROLES and new_role not in PRIVILEGED_ROLES:
        conn.close()
        raise HTTPException(status_code=400, detail="You cannot remove your own privileged role")
    if current_role in PRIVILEGED_ROLES and new_role not in PRIVILEGED_ROLES and is_active and _count_active_admins(conn) <= 1:
        conn.close()
        raise HTTPException(status_code=400, detail="At least one active admin must remain")
    if current_role == SUPER_ADMIN_ROLE and new_role != SUPER_ADMIN_ROLE and is_active and _count_active_super_admins(conn) <= 1:
        conn.close()
        raise HTTPException(status_code=400, detail="At least one active super admin must remain")

    conn.execute(
        "UPDATE app_users SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE lower(username) = lower(?)",
        (new_role, clean_username),
    )
    conn.close()
    _write_audit_log(admin_user["username"], "change_role", db_username, f"role={new_role}")
    return {"ok": True, "user": _public_user_payload(db_username)}


@router.post("/admin/users/{username}/status")
async def admin_change_status(username: str, request: Request, payload: Dict[str, Any] = Body(default={})):
    admin_user = require_admin(request)
    clean_username = _normalize_username(username)
    is_active = bool(payload.get("is_active"))

    conn = get_db_connection()
    row = conn.execute(
        "SELECT username, role, is_active FROM app_users WHERE lower(username) = lower(?) LIMIT 1",
        (clean_username,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    db_username, current_role, current_active = row
    current_role = str(current_role or "").strip().lower()
    try:
        _assert_can_manage_target_user(admin_user, current_role, "change status for")
    except HTTPException:
        conn.close()
        raise
    if bool(current_active) == is_active:
        conn.close()
        return {"ok": True, "user": _public_user_payload(db_username)}
    if db_username.lower() == admin_user["username"].lower() and not is_active:
        conn.close()
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    if current_role in PRIVILEGED_ROLES and not is_active and _count_active_admins(conn) <= 1:
        conn.close()
        raise HTTPException(status_code=400, detail="At least one active admin must remain")
    if current_role == SUPER_ADMIN_ROLE and not is_active and _count_active_super_admins(conn) <= 1:
        conn.close()
        raise HTTPException(status_code=400, detail="At least one active super admin must remain")

    conn.execute(
        "UPDATE app_users SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE lower(username) = lower(?)",
        (is_active, clean_username),
    )
    conn.close()
    _write_audit_log(admin_user["username"], "change_status", db_username, f"is_active={is_active}")
    return {"ok": True, "user": _public_user_payload(db_username)}


@router.delete("/admin/users/{username}")
async def admin_delete_user(username: str, request: Request):
    admin_user = require_admin(request)
    clean_username = _normalize_username(username)

    conn = get_db_connection()
    row = conn.execute(
        "SELECT username, role, is_active FROM app_users WHERE lower(username) = lower(?) LIMIT 1",
        (clean_username,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    db_username, current_role, is_active = row
    current_role = str(current_role or "").strip().lower()
    try:
        _assert_can_manage_target_user(admin_user, current_role, "delete")
    except HTTPException:
        conn.close()
        raise
    if db_username.lower() == admin_user["username"].lower():
        conn.close()
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if current_role in PRIVILEGED_ROLES and is_active and _count_active_admins(conn) <= 1:
        conn.close()
        raise HTTPException(status_code=400, detail="At least one active admin must remain")
    if current_role == SUPER_ADMIN_ROLE and is_active and _count_active_super_admins(conn) <= 1:
        conn.close()
        raise HTTPException(status_code=400, detail="At least one active super admin must remain")

    conn.execute("DELETE FROM app_users WHERE lower(username) = lower(?)", (clean_username,))
    conn.close()

    for token, session in list(sessions.items()):
        if str(session.get("username") or "").lower() == db_username.lower():
            sessions.pop(token, None)

    _write_audit_log(admin_user["username"], "delete_user", db_username)
    return {"ok": True}


@router.get("/admin/audit")
async def admin_audit_log(request: Request, limit: int = 12):
    require_admin(request)
    return {"items": _recent_audit_rows(limit=limit)}


@router.get("/admin/activity")
async def admin_activity_log(request: Request, limit: int = 20):
    require_admin(request)
    return {"items": _recent_activity_rows(limit=limit)}


@router.get("/admin/activity/{username}")
async def admin_user_activity_log(username: str, request: Request, limit: int = 20):
    require_admin(request)
    clean_username = _normalize_username(username)
    return {"items": _recent_activity_rows(limit=limit, username=clean_username)}
