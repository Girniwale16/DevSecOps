from typing import Any, Dict, Iterable, Optional


def extract_usage(data: Dict[str, Any]) -> Optional[Dict[str, int]]:
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None

    normalized: Dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if value is None:
            continue
        try:
            normalized[key] = int(value)
        except Exception:
            continue

    return normalized or None


def sum_usage(usages: Iterable[Optional[Dict[str, Any]]]) -> Optional[Dict[str, int]]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    found = False
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        for key in totals:
            value = usage.get(key)
            if value is None:
                continue
            try:
                totals[key] += int(value)
                found = True
            except Exception:
                continue
    return totals if found else None
