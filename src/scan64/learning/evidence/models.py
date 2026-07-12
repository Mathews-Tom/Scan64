from typing import Any

from pydantic import BaseModel
from sqlmodel import JSON, Column, Field, SQLModel


class Producer(BaseModel):
    name: str
    version: str


class Evidence(SQLModel, table=True):
    evidence_id: str = Field(primary_key=True)
    kind: str
    position_id: str
    engine_analysis_id: str
    claim: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    producer: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
