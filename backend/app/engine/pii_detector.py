import re
from typing import Any, Dict, List, Optional

try:
    import spacy  # type: ignore
except Exception:  # pragma: no cover
    spacy = None

_NLP = None


NAME_HINTS = {
    "name",
    "first_name",
    "last_name",
    "full_name",
    "customer_name",
    "employee_name",
}
EMAIL_HINTS = {"email", "mail", "e_mail"}
PHONE_HINTS = {"phone", "mobile", "contact", "telephone", "tel"}
SSN_HINTS = {"ssn", "social_security", "social_security_number", "tax_id", "pan"}
ADDRESS_HINTS = {"address", "street", "city", "state", "zip", "zipcode", "postal", "postcode"}
CARD_HINTS = {"card", "credit_card", "debit_card", "cc_num", "card_number", "cvv"}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\-\s()]{7,}[0-9]$")
SSN_RE = re.compile(r"^\d{3}-\d{2}-\d{4}$")
CARD_RE = re.compile(r"^\d{13,19}$")


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")


def _contains_any(tokenized_name: str, hints: set) -> bool:
    return any(h in tokenized_name for h in hints)


def _load_nlp():
    global _NLP
    if _NLP is not None:
        return _NLP
    if spacy is None:
        _NLP = False
        return _NLP
    try:
        _NLP = spacy.load("en_core_web_sm")
        return _NLP
    except Exception:
        _NLP = False
        return _NLP


def _name_based_signal(column_name: str) -> Dict[str, Any]:
    normalized = _normalize(column_name)
    if _contains_any(normalized, EMAIL_HINTS):
        return {"pii_type": "email", "score": 0.92, "reason": "column name suggests email"}
    if _contains_any(normalized, PHONE_HINTS):
        return {"pii_type": "phone", "score": 0.90, "reason": "column name suggests phone"}
    if _contains_any(normalized, SSN_HINTS):
        return {"pii_type": "ssn", "score": 0.96, "reason": "column name suggests ssn/tax id"}
    if _contains_any(normalized, CARD_HINTS):
        return {"pii_type": "card", "score": 0.93, "reason": "column name suggests payment card"}
    if _contains_any(normalized, ADDRESS_HINTS):
        return {"pii_type": "address", "score": 0.86, "reason": "column name suggests address/location"}
    if _contains_any(normalized, NAME_HINTS):
        return {"pii_type": "person_name", "score": 0.86, "reason": "column name suggests person name"}
    return {"pii_type": "unknown", "score": 0.0, "reason": "no strong name signal"}


def _regex_value_signal(values: List[str]) -> Dict[str, Any]:
    if not values:
        return {"pii_type": "unknown", "score": 0.0, "reason": "no sample values"}

    total = max(len(values), 1)
    email_hits = sum(1 for v in values if EMAIL_RE.match(v))
    phone_hits = 0
    for v in values:
        if PHONE_RE.match(v):
            digits = re.sub(r"\D+", "", v)
            # Avoid classifying short numeric patterns (e.g. SSN) as phone numbers.
            if len(digits) >= 10:
                phone_hits += 1
    ssn_hits = sum(1 for v in values if SSN_RE.match(v))
    card_hits = sum(1 for v in values if CARD_RE.match(v))

    ratios = {
        "email": email_hits / total,
        "phone": phone_hits / total,
        "ssn": ssn_hits / total,
        "card": card_hits / total,
    }
    pii_type, ratio = max(ratios.items(), key=lambda x: x[1])
    if ratio < 0.25:
        return {"pii_type": "unknown", "score": 0.0, "reason": "regex signal weak"}
    return {
        "pii_type": pii_type,
        "score": min(0.98, 0.55 + ratio * 0.45),
        "reason": f"regex matches in {int(ratio * 100)}% sample values",
    }


def _ner_signal(values: List[str]) -> Dict[str, Any]:
    nlp = _load_nlp()
    if not nlp or not values:
        return {"pii_type": "unknown", "score": 0.0, "reason": "spaCy model unavailable or no values"}

    person_hits = 0
    loc_hits = 0
    org_hits = 0
    total = 0
    for value in values[:80]:
        text = _to_text(value).strip()
        if not text:
            continue
        total += 1
        doc = nlp(text)
        labels = {ent.label_ for ent in doc.ents}
        if "PERSON" in labels:
            person_hits += 1
        if "GPE" in labels or "LOC" in labels:
            loc_hits += 1
        if "ORG" in labels:
            org_hits += 1

    if total == 0:
        return {"pii_type": "unknown", "score": 0.0, "reason": "no analyzable values"}

    ratios = {
        "person_name": person_hits / total,
        "address": loc_hits / total,
        "organization": org_hits / total,
    }
    pii_type, ratio = max(ratios.items(), key=lambda x: x[1])
    if ratio < 0.35:
        return {"pii_type": "unknown", "score": 0.0, "reason": "NER signal weak"}
    return {
        "pii_type": pii_type,
        "score": min(0.90, 0.50 + ratio * 0.40),
        "reason": f"NER detected {pii_type} in {int(ratio * 100)}% sample values",
    }


def _choose_signal(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    best = {"pii_type": "unknown", "score": 0.0, "reason": "no signal"}
    for s in signals:
        if s.get("score", 0.0) > best.get("score", 0.0):
            best = s
    return best


def detect_pii_columns(
    columns: List[Dict[str, Any]],
    value_samples: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, Any]]:
    value_samples = value_samples or {}
    results: List[Dict[str, Any]] = []

    for col in columns:
        col_id = _to_text(col.get("column_id"))
        col_name = _to_text(col.get("column_name"))
        table_name = _to_text(col.get("table_name"))
        dtype = _to_text(col.get("data_type"))

        samples = [_to_text(v).strip() for v in value_samples.get(col_id, []) if _to_text(v).strip()]
        name_signal = _name_based_signal(col_name)
        regex_signal = _regex_value_signal(samples)
        ner_signal = _ner_signal(samples)

        best = _choose_signal([name_signal, regex_signal, ner_signal])
        is_pii = best["score"] >= 0.60

        results.append(
            {
                "column_id": col_id,
                "table_name": table_name,
                "column_name": col_name,
                "data_type": dtype,
                "is_pii": bool(is_pii),
                "pii_type": best.get("pii_type", "unknown"),
                "confidence": float(round(best.get("score", 0.0), 4)),
                "reason": best.get("reason", "no reason"),
                "signal_breakdown": {
                    "name_score": float(round(name_signal.get("score", 0.0), 4)),
                    "regex_score": float(round(regex_signal.get("score", 0.0), 4)),
                    "ner_score": float(round(ner_signal.get("score", 0.0), 4)),
                },
            }
        )

    return results
