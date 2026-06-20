"""ORM models — the extensible data model for Bio 3D Arena.

Taxonomy (Category) and evaluation axes (Criterion) are first-class tables so new
biological categories and scoring criteria are added by inserting rows, not by
schema changes. `model_output.meta_json` is a free-form provenance bag.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Category(Base):
    """A biological taxonomy node: plants, flowers, proteins, cells, etc. Extensible."""

    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")

    tasks: Mapped[list["Task"]] = relationship(back_populates="category")


class Criterion(Base):
    """An evaluation axis: overall, realism, morphology, structural_accuracy, ..."""

    __tablename__ = "criterion"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")


class Generator(Base):
    """A model/system under evaluation. Anonymized while voting is in progress."""

    __tablename__ = "generator"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(
        String(64), default="model"
    )  # model | human | baseline
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=True)

    outputs: Mapped[list["ModelOutput"]] = relationship(back_populates="generator")


class Task(Base):
    """A benchmark task: a biological generation prompt + evaluation guidance."""

    __tablename__ = "task"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    prompt: Mapped[str] = mapped_column(Text)
    criteria_note: Mapped[str] = mapped_column(Text, default="")
    reference_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_output.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    category: Mapped["Category"] = relationship(back_populates="tasks")
    outputs: Mapped[list["ModelOutput"]] = relationship(
        back_populates="task", foreign_keys="ModelOutput.task_id"
    )


class ModelOutput(Base):
    """A single 3D asset produced by a generator for a task."""

    __tablename__ = "model_output"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    generator_id: Mapped[int] = mapped_column(ForeignKey("generator.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    asset_path: Mapped[str] = mapped_column(String(512))  # relative to ASSET_DIR
    asset_format: Mapped[str] = mapped_column(
        String(32), default="glb"
    )  # glb|gltf|pdb|...
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    n_comparisons: Mapped[int] = mapped_column(Integer, default=0)  # for matchmaking
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    task: Mapped["Task"] = relationship(
        back_populates="outputs", foreign_keys=[task_id]
    )
    generator: Mapped["Generator"] = relationship(back_populates="outputs")


class Comparison(Base):
    """A pair shown to a voter — the audit trail of what was presented."""

    __tablename__ = "comparison"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    output_a_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    output_b_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"))
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    vote: Mapped["Vote | None"] = relationship(
        back_populates="comparison", uselist=False
    )


class Vote(Base):
    """A recorded judgment for a comparison. winner ∈ {a, b, tie, bad}."""

    __tablename__ = "vote"

    id: Mapped[int] = mapped_column(primary_key=True)
    comparison_id: Mapped[int] = mapped_column(ForeignKey("comparison.id"), unique=True)
    winner: Mapped[str] = mapped_column(String(8))  # 'a' | 'b' | 'tie' | 'bad'
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    comparison: Mapped["Comparison"] = relationship(back_populates="vote")


class Rating(Base):
    """Cached ranking for (generator × scope × criterion).

    category_id NULL  → global (all categories).
    criterion_id      → which evaluation axis this rating is for.
    """

    __tablename__ = "rating"
    __table_args__ = (
        UniqueConstraint(
            "generator_id", "category_id", "criterion_id", name="uq_rating_scope"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    generator_id: Mapped[int] = mapped_column(ForeignKey("generator.id"), index=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True, index=True
    )
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    elo: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_score: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_lower: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_upper: Mapped[float] = mapped_column(Float, default=1000.0)
    n_games: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )
