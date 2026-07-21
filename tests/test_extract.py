"""The core tests. Every case can go red: each asserts a value this app
decides, and each negative would pass if the validator were dropped."""
import pytest

from app.extract import (
    extract_bank_accounts,
    extract_intel,
    extract_payids,
    normalise_abn,
    normalise_account,
    normalise_bsb,
    normalise_email,
    normalise_phone,
    normalise_sort_code,
)


# --- BSB (AU): 6 digits -> NNN-NNN --------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("062000", "062-000"),
    ("062-000", "062-000"),
    ("062 000", "062-000"),
    ("6200", None),       # too short -> rejected
    ("0620001", None),    # too long -> rejected
])
def test_normalise_bsb(raw, expected):
    assert normalise_bsb(raw) == expected


# --- UK sort code: 6 digits -> NN-NN-NN ---------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("123456", "12-34-56"),
    ("12-34-56", "12-34-56"),
    ("12 34 56", "12-34-56"),
    ("12345", None),      # too short -> rejected
])
def test_normalise_sort_code(raw, expected):
    assert normalise_sort_code(raw) == expected


# --- account number: digits, 5-10 ---------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("12345678", "12345678"),
    ("1234 5678", "12345678"),
    ("1234", None),           # too short -> rejected
    ("12345678901", None),    # too long -> rejected
])
def test_normalise_account(raw, expected):
    assert normalise_account(raw) == expected


# --- email: deobfuscate + lowercase -------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("John@Example.COM", "john@example.com"),
    ("john [at] example [dot] com", "john@example.com"),
    ("john at example dot com", "john@example.com"),
    ("not-an-email", None),
])
def test_normalise_email(raw, expected):
    assert normalise_email(raw) == expected


# --- phone: AU/UK -> E.164 ----------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("0412 345 678", "+61412345678"),      # AU mobile
    ("+61 412 345 678", "+61412345678"),
    ("07911 123456", "+447911123456"),     # UK mobile
    ("12345678", None),                    # not a valid number -> rejected
    ("0123456789", None),                  # a 10-digit account run, not a phone
])
def test_normalise_phone(raw, expected):
    assert normalise_phone(raw) == expected


# --- ABN: 11 digits + modulus-89 checksum -------------------------------
def test_normalise_abn_valid():
    assert normalise_abn("51 824 753 556") == "51824753556"


def test_normalise_abn_bad_checksum():
    # one digit mutated -> checksum must reject (this is what the checksum buys)
    assert normalise_abn("51824753557") is None


def test_normalise_abn_wrong_length():
    assert normalise_abn("5182475355") is None


# --- extraction + pairing ------------------------------------------------
def test_bsb_paired_with_account():
    got = extract_bank_accounts("pay to bsb 062-000 acct 12345678 thanks")
    assert got == [{"scheme": "AU_BSB", "routing": "062-000", "account_number": "12345678"}]


def test_sort_code_scheme_detected_by_shape():
    got = extract_bank_accounts("UK: 12-34-56 account 87654321")
    assert got == [{"scheme": "UK_SORT", "routing": "12-34-56", "account_number": "87654321"}]


def test_routing_without_account_still_captured():
    got = extract_bank_accounts("my bsb is 062-000")
    assert got == [{"scheme": "AU_BSB", "routing": "062-000", "account_number": None}]


# --- PayID dispatch ------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("PayID: flagged@example.com", {"kind": "email", "value": "flagged@example.com"}),
    ("payid is 0412 345 678", {"kind": "phone", "value": "+61412345678"}),
    ("PayID 51 824 753 556", {"kind": "abn", "value": "51824753556"}),
])
def test_payid_dispatch(text, expected):
    assert extract_payids(text) == [expected]


# --- dedup across messages ----------------------------------------------
def test_dedup_across_messages():
    intel = extract_intel(["contact flagged@example.com", "again: flagged@example.com"])
    assert intel["emails"] == ["flagged@example.com"]


def test_dedup_normalises_before_comparing():
    intel = extract_intel(["bsb 062000 acct 12345678", "bsb 062-000 acct 12345678"])
    assert len(intel["bank_accounts"]) == 1


# --- conversation shape: a growing, multi-message conversation ----------
def test_intel_merges_across_a_growing_conversation():
    # the production shape: different intel lands in different messages
    intel = extract_intel([
        "hi, email me at a@b.com",
        "and call 0412 345 678",
        "then pay bsb 062-000 acct 12345678",
    ])
    assert intel["emails"] == ["a@b.com"]
    assert intel["phones"] == ["+61412345678"]
    assert intel["bank_accounts"] == [
        {"scheme": "AU_BSB", "routing": "062-000", "account_number": "12345678"}
    ]


def test_email_that_is_also_a_payid_appears_in_both():
    # an address declared as a PayID is genuinely both — a raw email and a PayID
    intel = extract_intel(["contact flagged@example.com", "PayID: flagged@example.com"])
    assert intel["emails"] == ["flagged@example.com"]
    assert intel["payids"] == [{"kind": "email", "value": "flagged@example.com"}]


def test_sort_code_keyword_form():
    got = extract_bank_accounts("please use sort code 12 34 56 account 87654321")
    assert got == [{"scheme": "UK_SORT", "routing": "12-34-56", "account_number": "87654321"}]


def test_payid_with_unrecognisable_value_is_not_classified():
    assert extract_payids("my PayID is total nonsense here") == []


def test_payid_binds_to_leftmost_identifier_not_trailing_text():
    # the ABN is the PayID; the phone later in the line must not hijack it
    got = extract_payids("PayID 51 824 753 556, call +44 7911 123456")
    assert got == [{"kind": "abn", "value": "51824753556"}]
