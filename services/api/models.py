from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

VALID_CATEGORIES = [
    "medical_supplies",
    "laboratory_services",
    "pharmaceutical",
    "clinical_software",
    "it_infrastructure",
    "hr_and_payroll_software",
    "cleaning_and_facilities",
    "patient_communication",
    "billing_and_coding_software",
    "training_platforms",
]


class Country(str, Enum):
    USA = "USA"
    UK = "UK"


class Currency(str, Enum):
    USD = "USD"
    GBP = "GBP"


class SupplierStatus(str, Enum):
    active = "active"
    suspended = "suspended"


class ComplianceAgreement(str, Enum):
    BAA = "BAA"
    DPA = "DPA"
    both = "both"


class SupplierBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    country: Country
    categories: list[str] = Field(min_length=1)
    monthly_rate: float = Field(gt=0)
    currency: Currency
    status: SupplierStatus
    compliance_agreement: Optional[ComplianceAgreement] = None
    contract_renewal_date: Optional[date] = None
    contact_email: Optional[EmailStr] = None
    notes: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_business_rules(self) -> "SupplierBase":
        invalid_categories = [value for value in self.categories if value not in VALID_CATEGORIES]
        if invalid_categories:
            raise ValueError(f"Invalid categories: {', '.join(invalid_categories)}")

        if self.country == Country.USA and self.currency != Currency.USD:
            raise ValueError("Suppliers from USA must use currency USD")

        if self.country == Country.UK and self.currency != Currency.GBP:
            raise ValueError("Suppliers from UK must use currency GBP")

        return self


class SupplierCreate(SupplierBase):
    pass


class Supplier(SupplierBase):
    id: int
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SupplierRateUpdate(BaseModel):
    monthly_rate: float = Field(gt=0)


class SupplierStatusUpdate(BaseModel):
    status: SupplierStatus


class SupplierSeed(SupplierBase):
    def with_defaults(self) -> dict:
        payload = self.model_dump(mode="json", exclude_none=True)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        return payload


class UserRole(str, Enum):
    admin = "admin"
    manager = "manager"
    user = "user"


class ProfileBase(BaseModel):
    name: Optional[str] = Field(default=None, max_length=160)
    phone: Optional[str] = Field(default=None, max_length=40)
    address: Optional[str] = Field(default=None, max_length=300)


class ProfileCreate(ProfileBase):
    pass


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=160)
    phone: Optional[str] = Field(default=None, max_length=40)
    address: Optional[str] = Field(default=None, max_length=300)


class ProfileRead(ProfileBase):
    id: str
    user_id: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    name: Optional[str] = Field(default=None, max_length=160)
    phone: Optional[str] = Field(default=None, max_length=40)
    address: Optional[str] = Field(default=None, max_length=300)


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None


class UserPublic(BaseModel):
    id: str
    email: EmailStr
    is_active: bool
    role: UserRole
    created_at: datetime


class UserWithProfile(BaseModel):
    id: str
    email: EmailStr
    is_active: bool
    role: UserRole
    created_at: datetime
    profile: ProfileRead


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthMeResponse(BaseModel):
    id: str
    email: EmailStr
    role: UserRole
    profile: ProfileRead
