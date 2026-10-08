from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

TaskType = Literal[
    "INFORMATION", "FILE_UPLOAD", "FORM", "APPROVAL",
    "INTERNAL_TASK", "CLIENT_CONFIRMATION", "SIGNATURE", "QUESTIONNAIRE",
]
Priority = Literal["low", "medium", "high"]
Audience = Literal["client", "agency"]

Password = Field(min_length=8, max_length=64)


class RegisterIn(BaseModel):
    organization_name: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Password


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenIn(BaseModel):
    token: str


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    new_password: str = Password


class AcceptInviteIn(BaseModel):
    token: str
    password: str = Password


class TeamMemberIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Password
    role: Literal["team_member", "org_admin"] = "team_member"


class TeamMemberUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    role: Optional[Literal["team_member", "org_admin"]] = None
    status: Optional[Literal["active", "disabled"]] = None


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    company_name: str = Field(default="", max_length=160)
    email: EmailStr
    phone: str = Field(default="", max_length=40)
    industry: str = Field(default="", max_length=120)
    website: str = Field(default="", max_length=255)
    address: str = Field(default="", max_length=1000)
    assigned_user_id: Optional[int] = None


class ClientUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    company_name: Optional[str] = Field(default=None, max_length=160)
    phone: Optional[str] = Field(default=None, max_length=40)
    industry: Optional[str] = Field(default=None, max_length=120)
    website: Optional[str] = Field(default=None, max_length=255)
    address: Optional[str] = Field(default=None, max_length=1000)
    assigned_user_id: Optional[int] = None
    status: Optional[Literal["Archived", "Onboarding"]] = None


class TemplateTaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    type: TaskType = "INFORMATION"
    audience: Audience = "client"
    priority: Priority = "medium"
    is_required: bool = True
    due_in_days: Optional[int] = Field(default=None, ge=0, le=3650)
    config: dict[str, Any] = Field(default_factory=dict)


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    tasks: list[TemplateTaskIn] = Field(default_factory=list, max_length=200)


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    tasks: Optional[list[TemplateTaskIn]] = Field(default=None, max_length=200)


class ProjectIn(BaseModel):
    client_id: int
    template_id: Optional[int] = None
    name: Optional[str] = Field(default=None, max_length=200)
    due_date: Optional[date] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    due_date: Optional[date] = None
    status: Optional[Literal["ACTIVE", "ARCHIVED"]] = None


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    type: TaskType = "INFORMATION"
    audience: Audience = "client"
    priority: Priority = "medium"
    is_required: bool = True
    due_date: Optional[date] = None
    config: dict[str, Any] = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    priority: Optional[Priority] = None
    is_required: Optional[bool] = None
    due_date: Optional[date] = None
    assigned_to: Optional[int] = None


class SubmitIn(BaseModel):
    content: dict[str, Any] = Field(default_factory=dict)
    comment: str = Field(default="", max_length=4000)
    deadline: Optional[date] = None  # used when staff request a client approval

    @field_validator("content")
    @classmethod
    def _small(cls, v):
        import json
        if len(json.dumps(v)) > 100_000:
            raise ValueError("Submission is too large")
        return v


class DecisionIn(BaseModel):
    comment: str = Field(default="", max_length=4000)


class ApprovalRequestIn(BaseModel):
    task_id: int
    comment: str = Field(default="", max_length=4000)
    deadline: Optional[date] = None


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    is_internal: bool = False
