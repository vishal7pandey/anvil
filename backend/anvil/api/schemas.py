from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MergePolicy = Literal["human", "agent-after-approval", "agent"]


class SessionCreate(BaseModel):
    repo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
    environment: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    mode: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
    model_profile: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    merge_policy: MergePolicy = "human"
    title: str | None = Field(default=None, max_length=512)


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


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    trigger: str
    repo: str
    status: str
    mode: str
    base_sha: str | None
    branch: str | None
    sandbox_image_digest: str | None
    model_aliases: dict[str, Any]
    skill_versions: dict[str, Any]
    prompt_hash: str | None
    budgets: dict[str, Any]
    started_at: datetime | None
    ended_at: datetime | None
    usd: float
    input_tokens: int
    output_tokens: int
    human_touches: int
    artifacts: list[dict[str, Any]]
    cancel_requested_at: datetime | None
    error: str | None
    created_at: datetime


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


class IntakeResponse(BaseModel):
    accepted: int
    run_ids: list[UUID]
