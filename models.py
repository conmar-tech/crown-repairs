"""API request models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


OrderStatus = Literal["New", "InWork", "AtJeweler", "Ready", "PickedUp"]


class GoogleCredential(BaseModel):
    credential: str = Field(min_length=20)


class StatusPayload(BaseModel):
    status: OrderStatus
    settleBalance: bool = False


class PaymentPayload(BaseModel):
    totalPriceCents: int = Field(ge=0)
    depositPaidCents: int = Field(ge=0)
