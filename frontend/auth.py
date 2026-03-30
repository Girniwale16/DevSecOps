from typing import Any, Dict, Optional


def get_auth_token(user_storage: Dict[str, Any]) -> str:
    return str(user_storage.get("auth_token") or "").strip()


def get_auth_user(user_storage: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    user = user_storage.get("auth_user")
    return user if isinstance(user, dict) else None


def is_authenticated(user_storage: Dict[str, Any]) -> bool:
    return bool(get_auth_token(user_storage))


def set_auth_state(user_storage: Dict[str, Any], token: str, user: Dict[str, Any]) -> None:
    user_storage["auth_token"] = str(token or "").strip()
    user_storage["auth_user"] = dict(user or {})


def clear_auth_state(user_storage: Dict[str, Any]) -> None:
    user_storage["auth_token"] = ""
    user_storage["auth_user"] = None


def build_auth_headers(token: str) -> Dict[str, str]:
    clean = str(token or "").strip()
    if not clean:
        return {}
    return {"Authorization": f"Bearer {clean}"}
