from datetime import datetime
import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MergePolicy = Literal["human", "agent-after-approval", "agent"]
CostStatus = Literal["pending", "available", "missing"]


def normalize_repository(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Repository must be a GitHub owner/repository")
    repository = value.strip()
    if "://" in repository:
        parsed = urlsplit(repository)
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Repository URL must use https://github.com")
        repository = parsed.path.strip("/")
    elif repository.casefold().startswith("github.com/"):
        repository = repository[len("github.com/") :]
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Repository must be a GitHub owner/repository")
    return repository


class SessionCreate(BaseModel):
    repo: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=3,
            max_length=512,
            pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
        ),
    ]
    environment: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    mode: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
    model_profile: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    merge_policy: MergePolicy = "human"
    title: str | None = Field(default=None, max_length=512)

    @field_validator("repo", mode="before")
    @classmethod
    def normalize_repo(cls, value: str) -> str:
        return normalize_repository(value)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repo: str
    environment: str
    mode: str
    merge_policy: str
    model_profile: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
    scenario: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] | None = None


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    trigger: str
    repo: str
    status: str
    mode: str
    network: str
    base_ref: str
    jira_key: str | None
    base_sha: str | None
    branch: str | None
    sandbox_image_digest: str | None
    model_aliases: dict[str, Any]
    skill_versions: dict[str, Any]
    prompt_hash: str | None
    budgets: dict[str, Any]
    started_at: datetime | None
    ended_at: datetime | None
    usd: float | None
    input_tokens: int
    output_tokens: int
    human_touches: int
    wall_time_seconds: float | None = None
    model_calls: int | None = None
    cost_usd: float | None = None
    cost_status: CostStatus = "pending"
    ci_rounds: int = 0
    outcome: str | None = None
    artifacts: list[dict[str, Any]]
    run_record: dict[str, Any]
    cancel_requested_at: datetime | None
    error: str | None
    created_at: datetime


class MetricsResponse(BaseModel):
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None
    scenario: str | None
    run_count: int
    total_wall_time_seconds: float
    average_wall_time_seconds: float | None
    model_calls: int | None
    input_tokens: int
    output_tokens: int
    usd: float | None
    cost_usd: float | None = None
    cost_status: Literal["available", "missing"] = "available"
    human_touches: int
    ci_rounds: int
    outcomes: dict[str, int]


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    seq: int
    ts: datetime
    type: str
    agent: str | None
    data: dict[str, Any]


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=4000)


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    action: str
    args: dict[str, Any]
    reason: str | None
    status: str
    comment: str | None
    requested_at: datetime
    decided_at: datetime | None


class ApprovalInboxResponse(BaseModel):
    approval_id: UUID
    run_id: UUID
    session_id: UUID
    session_title: str | None
    repo: str
    run_status: str
    action: str
    args: dict[str, Any]
    reason: str | None
    agent: str | None
    requested_at: datetime


class IntakeResponse(BaseModel):
    accepted: int
    run_ids: list[UUID]
