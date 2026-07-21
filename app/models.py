"""Request/response models.

The intel value objects enforce their own invariants: a `BankAccount` or
`PayID` cannot exist in an invalid shape. Validation reuses the same
`normalise_*` functions the extractor uses, so the type *is* the guarantee at
the boundary — the caller's discipline isn't what keeps bad data out.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator
from typing_extensions import Literal

from .extract import (
    normalise_abn,
    normalise_account,
    normalise_bsb,
    normalise_email,
    normalise_phone,
    normalise_sort_code,
)


MAX_MESSAGES = 500
MAX_MESSAGE_CHARS = 10_000


class Message(BaseModel):
    sender: str
    text: str = Field(max_length=MAX_MESSAGE_CHARS)
    timestamp: Optional[datetime] = None


class Conversation(BaseModel):
    conversation_id: str
    channel: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    messages: List[Message] = Field(min_length=1, max_length=MAX_MESSAGES)


class BankAccount(BaseModel):
    # `routing` holds the BSB (NNN-NNN) or sort code (NN-NN-NN); one generic
    # field keeps the model flat and open to other schemes.
    scheme: Literal["AU_BSB", "UK_SORT"]
    routing: str
    account_number: Optional[str] = None

    @model_validator(mode="after")
    def _validate(self) -> "BankAccount":
        normalise = normalise_bsb if self.scheme == "AU_BSB" else normalise_sort_code
        if normalise(self.routing) != self.routing:
            raise ValueError(f"invalid {self.scheme} routing: {self.routing!r}")
        if self.account_number and normalise_account(self.account_number) != self.account_number:
            raise ValueError(f"invalid account_number: {self.account_number!r}")
        return self


class PayID(BaseModel):
    kind: Literal["email", "phone", "abn"]
    value: str

    @model_validator(mode="after")
    def _validate(self) -> "PayID":
        normalise = {
            "email": normalise_email,
            "phone": normalise_phone,
            "abn": normalise_abn,
        }[self.kind]
        if normalise(self.value) != self.value:
            raise ValueError(f"invalid {self.kind} PayID: {self.value!r}")
        return self


class Intel(BaseModel):
    bank_accounts: List[BankAccount] = Field(default_factory=list)
    emails: List[str] = Field(default_factory=list)
    phones: List[str] = Field(default_factory=list)
    payids: List[PayID] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    conversation_id: str
    scam_probability: Optional[float] = None
    intel: Intel
    cached: bool = False
    llm_status: Literal["ok", "unavailable"] = "ok"
