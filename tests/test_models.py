"""The value objects enforce their own invariants — illegal states can't be
constructed. Each negative would pass if the model validator were removed."""
import pytest
from pydantic import ValidationError

from app.models import BankAccount, PayID


def test_bank_account_rejects_malformed_routing():
    with pytest.raises(ValidationError):
        BankAccount(scheme="AU_BSB", routing="garbage")


def test_bank_account_rejects_routing_wrong_for_scheme():
    # a valid sort-code shape is not a valid BSB shape
    with pytest.raises(ValidationError):
        BankAccount(scheme="AU_BSB", routing="12-34-56")


def test_bank_account_accepts_valid():
    ba = BankAccount(scheme="AU_BSB", routing="062-000", account_number="12345678")
    assert ba.routing == "062-000"


def test_payid_rejects_value_not_matching_kind():
    with pytest.raises(ValidationError):
        PayID(kind="abn", value="not-an-abn")


def test_payid_accepts_valid_email():
    assert PayID(kind="email", value="flagged@example.com").value == "flagged@example.com"
