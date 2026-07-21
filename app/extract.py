"""Deterministic extraction + validation/normalisation — the trust boundary.

Nothing becomes intel until it passes a validator: the regexes *propose*
candidates, the `normalise_*` functions *decide*. Pure, no I/O, no LLM, so it
is cheap enough to re-run on every message and fully unit-testable.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import phonenumbers
from email_validator import EmailNotValidError, validate_email
from stdnum.au import abn as _abn
from stdnum.exceptions import ValidationError as _StdnumError

# Identifier formats/checksums come from libraries, not hand-rolled logic:
# phone -> phonenumbers, ABN -> python-stdnum, email -> email-validator.
_PHONE_REGIONS = ("AU", "GB")


# --------------------------------------------------------------------------
# Validators / normalisers — each returns a normalised value or None (reject).
# --------------------------------------------------------------------------

def normalise_bsb(raw: str) -> Optional[str]:
    """AU BSB: 6 digits -> NNN-NNN."""
    d = re.sub(r"\D", "", raw)
    return f"{d[:3]}-{d[3:]}" if len(d) == 6 else None


def normalise_sort_code(raw: str) -> Optional[str]:
    """UK sort code: 6 digits -> NN-NN-NN."""
    d = re.sub(r"\D", "", raw)
    return f"{d[:2]}-{d[2:4]}-{d[4:]}" if len(d) == 6 else None


def normalise_account(raw: str) -> Optional[str]:
    """Bank account: digits only, 5-10 long (covers AU 6-10 and UK 8)."""
    d = re.sub(r"\D", "", raw)
    return d if 5 <= len(d) <= 10 else None


def normalise_email(raw: str) -> Optional[str]:
    m = _EMAIL_RE.search(_deobfuscate(raw))
    return _validate_email(m.group(0)) if m else None


def normalise_phone(raw: str) -> Optional[str]:
    """A single value -> E.164 via phonenumbers, tried as AU then UK. The
    library owns validity (a 10-digit account run like 0123456789 is rejected)."""
    for region in _PHONE_REGIONS:
        try:
            number = phonenumbers.parse(raw, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(number):
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return None


def normalise_abn(raw: str) -> Optional[str]:
    """Australian Business Number — validated (incl. checksum) by python-stdnum."""
    try:
        return _abn.validate(raw)
    except _StdnumError:
        return None


def _validate_email(candidate: str) -> Optional[str]:
    try:
        # check_deliverability=False -> no DNS lookup (offline, fast).
        # lowercased for case-insensitive dedup of the same address.
        return validate_email(candidate, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


# --------------------------------------------------------------------------
# Candidate finders
# --------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_BSB_HYPHEN = re.compile(r"\b\d{3}-\d{3}\b")
_SORT_HYPHEN = re.compile(r"\b\d{2}-\d{2}-\d{2}\b")
_BSB_KW = re.compile(r"bsb\D{0,4}(\d[\d\s-]{4,8}\d)", re.I)
_SORT_KW = re.compile(r"sort\s*code\D{0,4}(\d[\d\s-]{4,8}\d)", re.I)
_ACCOUNT_KW = re.compile(r"(?:acc(?:oun)?t|a/?c)\D{0,4}(\d[\d\s]{3,10}\d)", re.I)
# Bare (unanchored) digit runs need 6+ digits: shorter runs are usually dollar
# amounts, not account numbers. Keyword-anchored accounts still allow 5 digits.
_DIGIT_RUN = re.compile(r"\b\d{6,10}\b")
_PAYID_RE = re.compile(r"pay\s*id\b\s*(?:is|:|-|=)?\s*(.+)", re.I)


def _deobfuscate(text: str) -> str:
    """Undo the common email obfuscations only (bounded, not an arms race).
    Surrounding whitespace is consumed so the address becomes contiguous."""
    t = re.sub(r"\s*[\(\[\{]\s*at\s*[\)\]\}]\s*", "@", text, flags=re.I)
    t = re.sub(r"\s*[\(\[\{]\s*dot\s*[\)\]\}]\s*", ".", t, flags=re.I)
    t = re.sub(r"\s+at\s+", "@", t, flags=re.I)
    t = re.sub(r"\s+dot\s+", ".", t, flags=re.I)
    return t


def extract_emails(text: str) -> List[str]:
    # a regex still *finds* candidates in free text; email-validator decides.
    out = []
    for m in _EMAIL_RE.finditer(_deobfuscate(text)):
        email = _validate_email(m.group(0))
        if email:
            out.append(email)
    return out


def extract_phones(text: str) -> List[str]:
    # phonenumbers finds valid numbers in free text directly (no hand-rolled regex).
    out = []
    for region in _PHONE_REGIONS:
        for match in phonenumbers.PhoneNumberMatcher(text, region):
            out.append(phonenumbers.format_number(
                match.number, phonenumbers.PhoneNumberFormat.E164))
    return out


def extract_bank_accounts(text: str) -> List[Dict]:
    """Find routing numbers (BSB / sort code) and pair each with the nearest
    account number in the same message. Proximity pairing only — cross-message
    pairing is a deliberate non-goal."""
    found = []  # (scheme, normalised, start, end)
    for m in _BSB_HYPHEN.finditer(text):
        found.append(("AU_BSB", normalise_bsb(m.group(0)), m.start(), m.end()))
    for m in _SORT_HYPHEN.finditer(text):
        found.append(("UK_SORT", normalise_sort_code(m.group(0)), m.start(), m.end()))
    for m in _BSB_KW.finditer(text):
        found.append(("AU_BSB", normalise_bsb(m.group(1)), m.start(), m.end()))
    for m in _SORT_KW.finditer(text):
        found.append(("UK_SORT", normalise_sort_code(m.group(1)), m.start(), m.end()))

    # One routing per (scheme, value): the hyphen and keyword forms overlap.
    routings, routing_spans = [], []
    seen = set()
    for scheme, value, start, end in found:
        if value and (scheme, value) not in seen:
            seen.add((scheme, value))
            routings.append((scheme, value, start))
        if value:
            routing_spans.append((start, end))

    accounts = []  # (normalised, position), routing digits excluded
    for m in _ACCOUNT_KW.finditer(text):
        acct = normalise_account(m.group(1))
        if acct:
            accounts.append((acct, m.start(1)))
    for m in _DIGIT_RUN.finditer(text):
        if any(s <= m.start() < e for s, e in routing_spans):
            continue  # this digit run is part of a routing number
        acct = normalise_account(m.group(0))
        if acct and acct not in {a[0] for a in accounts}:
            accounts.append((acct, m.start()))

    pool = list(accounts)
    out = []
    for scheme, routing, pos in routings:
        account = None
        if pool:
            nearest = min(pool, key=lambda a: abs(a[1] - pos))
            account = nearest[0]
            pool.remove(nearest)
        out.append({"scheme": scheme, "routing": routing, "account_number": account})
    return out


def extract_payids(text: str) -> List[Dict]:
    """A PayID is an email, phone, or ABN. Keyword-driven so we do not guess."""
    out = []
    for m in _PAYID_RE.finditer(text):
        remainder = m.group(1)
        classified = _classify_payid(remainder)
        if classified:
            out.append(classified)
    return out


def _classify_payid(remainder: str) -> Optional[Dict]:
    """A PayID is the identifier right after the keyword. The keyword capture
    runs to end-of-line, so pick the *leftmost* valid identifier — otherwise a
    later phone/email in "PayID <abn>, call <phone>" would hijack the value."""
    candidates = []  # (position, kind, normalised value)

    em = _EMAIL_RE.search(remainder)
    if em:
        email = _validate_email(em.group(0))
        if email:
            candidates.append((em.start(), "email", email))

    for m in re.finditer(r"\d[\d\s]{9,}\d", remainder):  # spaced 11-digit ABN
        abn = normalise_abn(m.group(0))
        if abn:
            candidates.append((m.start(), "abn", abn))
            break

    for region in _PHONE_REGIONS:
        for match in phonenumbers.PhoneNumberMatcher(remainder, region):
            e164 = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
            candidates.append((match.start, "phone", e164))
            break

    if not candidates:
        return None
    _, kind, value = min(candidates, key=lambda c: c[0])
    return {"kind": kind, "value": value}


# --------------------------------------------------------------------------
# Top level: run every extractor over every message, merge, dedup.
# --------------------------------------------------------------------------

def extract_intel(texts: List[str]) -> Dict:
    banks: List[Dict] = []
    emails: List[str] = []
    phones: List[str] = []
    payids: List[Dict] = []
    for t in texts:
        banks += extract_bank_accounts(t)
        emails += extract_emails(t)
        phones += extract_phones(t)
        payids += extract_payids(t)
    return {
        "bank_accounts": _dedup_dicts(banks),
        "emails": _dedup(emails),
        "phones": _dedup(phones),
        "payids": _dedup_dicts(payids),
    }


def _dedup(items: List[str]) -> List[str]:
    return list(dict.fromkeys(items))  # order-preserving unique


def _dedup_dicts(items: List[Dict]) -> List[Dict]:
    seen, out = set(), []
    for i in items:
        key = tuple(sorted(i.items()))
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out
