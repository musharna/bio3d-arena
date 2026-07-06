"""Pydantic request/response schemas for the JSON API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VoteIn(BaseModel):
    comparison_id: int
    winner: str = Field(pattern="^(a|b|tie|bad)$")


class KVoteIn(BaseModel):
    ballot_id: int
    best_output_id: int | None = None


class FlagIn(BaseModel):
    output_id: int
    reason: str = Field(default="not_the_organism", pattern="^(not_the_organism|failed|other)$")


class CategoryIn(BaseModel):
    slug: str
    name: str
    description: str = ""


class CriterionIn(BaseModel):
    slug: str
    name: str
    description: str = ""


class GeneratorIn(BaseModel):
    slug: str
    name: str
    description: str = ""
    kind: str = "model"
    is_anonymous: bool = True


class TaskIn(BaseModel):
    category: str  # category slug
    title: str
    prompt: str
    criteria_note: str = ""
