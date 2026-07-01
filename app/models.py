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
    kind: Mapped[str] = mapped_column(String(64), default="model")  # model | human | baseline
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
    asset_format: Mapped[str] = mapped_column(String(32), default="glb")  # glb|gltf|pdb|...
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    n_comparisons: Mapped[int] = mapped_column(Integer, default=0)  # for matchmaking
    # Gold/decoy assets used only for attention checks — excluded from matchmaking
    # and from all rankings.
    is_gold: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    # Provenance (readies externally-sourced models). For our own assets:
    # source="bio3d-arena", external_url=None (null ⇒ hosted locally).
    source: Mapped[str] = mapped_column(String(64), default="bio3d-arena")
    license: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attribution: Mapped[str | None] = mapped_column(String(256), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    task: Mapped["Task"] = relationship(back_populates="outputs", foreign_keys=[task_id])
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
    # Gold attention check: is_gold set when this pair has a known-correct answer
    # (gold_expected ∈ {'a','b'} = the slot holding the good asset).
    is_gold: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    gold_expected: Mapped[str | None] = mapped_column(String(1), nullable=True)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    vote: Mapped["Vote | None"] = relationship(back_populates="comparison", uselist=False)


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
        UniqueConstraint("generator_id", "category_id", "criterion_id", name="uq_rating_scope"),
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
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class User(Base):
    """A verified voter, identified by their Hugging Face account (OAuth)."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    hf_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class VoterSession(Base):
    """Trust bookkeeping for an anonymous voter (keyed by the session cookie).

    trust starts at 1.0 (benefit of the doubt) and is Laplace-smoothed by
    gold attention-check outcomes: trust = (gold_passed + 1) / (gold_seen + 1).
    Votes from sessions below TRUST_THRESHOLD are excluded from the authoritative
    Bradley-Terry leaderboard (but still recorded).
    """

    __tablename__ = "voter_session"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trust: Mapped[float] = mapped_column(Float, default=1.0)
    gold_seen: Mapped[int] = mapped_column(Integer, default=0)
    gold_passed: Mapped[int] = mapped_column(Integer, default=0)
    n_votes: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    # Verified-login link: NULL = anonymous, set = signed in with Hugging Face (SP2).
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)


class GoldPair(Base):
    """A calibration pair with a known-correct answer (good vs decoy output).

    Both referenced outputs have model_output.is_gold = True so they never enter
    normal matchmaking or rankings.
    """

    __tablename__ = "gold_pair"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    good_output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    bad_output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    note: Mapped[str] = mapped_column(Text, default="")


class Submission(Base):
    """A community-submitted 3D output awaiting moderation before entering the arena.

    The asset is validated + stored at submit time; on approval a ModelOutput is
    created that reuses the stored blob (no re-upload).
    """

    __tablename__ = "submission"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    generator_slug: Mapped[str] = mapped_column(String(64))
    generator_name: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    asset_path: Mapped[str] = mapped_column(String(512))
    asset_format: Mapped[str] = mapped_column(String(32), default="glb")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    submitter: Mapped[str] = mapped_column(String(200), default="")  # free-form name/contact
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending | approved | rejected
    review_note: Mapped[str] = mapped_column(Text, default="")
    model_output_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_output.id"), nullable=True
    )
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Metric(Base):
    """Objective recon-accuracy score for one ModelOutput vs a task's GT cloud set.

    Mode-B counterpart to the vote-driven Rating. Populated by the batch scorer
    (recon_service) from AgriGen's /score microservice. One row per output (latest);
    rescoring overwrites. Confounds + versions are typed columns (fairness §6.2/§6.1
    of the recon-integration spec) so the board is reproducible and audit-able.
    """

    __tablename__ = "metric"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), unique=True, index=True)
    # Holistic measures (never rank on chamfer alone).
    chamfer: Mapped[float | None] = mapped_column(Float, nullable=True)
    nearest_shape_distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    nearest_gt_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    tau: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    species_verdict: Mapped[str | None] = mapped_column(String(8), nullable=True)  # PASS|FAIL
    gt_band_lo: Mapped[float | None] = mapped_column(Float, nullable=True)
    gt_band_hi: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Pinned confounds + reproducibility.
    point_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    icp_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scorer_version: Mapped[str] = mapped_column(String(128), default="")
    gt_version_hash: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error|skipped
    detail: Mapped[str] = mapped_column(Text, default="")
    computed: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class OrganMetric(Base):
    """Mode-B *organ-structure* fidelity for one ModelOutput — the second objective axis
    beside chamfer (recon appearance). Higher = better (∈[0,1]); chamfer is lower=better.

    Populated by structure_service from AgriGen's /score_structure microservice, for
    STRUCTURE-KNOWN (procedural) outputs only — i.e. outputs that carry a declared organ
    record (counts/angles). Recon/scan/found/volumetric outputs have NO row here (no
    declared structure) and render "—". A separate table (not Metric columns) mirrors the
    ReconTask convention: the schema is create_all-only, so a new table is picked up but a
    new column on the existing `metric` table would not be (no ALTER).

    `status` disambiguates a NULL `botanical_fidelity`:
      scored        — graded against the reference (value present; an honest 0.0 = a real
                       structural gap, a valid finding, NOT an error).
      no_reference  — species has no botanical reference yet (only 5 covered) → honest N/A.
      error         — the scoring service was unreachable / returned non-2xx (offline).
    """

    __tablename__ = "organ_metric"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), unique=True, index=True)
    species_slug: Mapped[str] = mapped_column(String(64), default="")  # resolved record species
    botanical_fidelity: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )  # [0,1], ↑ better
    n_attributes: Mapped[int] = mapped_column(Integer, default=0)
    attributes: Mapped[str] = mapped_column(Text, default="{}")  # per-attr explain detail (JSON)
    note: Mapped[str] = mapped_column(
        Text, default=""
    )  # service note (e.g. "no botanical reference")
    metric_version: Mapped[str] = mapped_column(String(64), default="botanical_organ_fidelity_v1")
    status: Mapped[str] = mapped_column(String(16), default="scored")  # scored|no_reference|error
    detail: Mapped[str] = mapped_column(Text, default="")
    computed: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Critique(Base):
    """Per-output Spotlight render + qualitative/perceptual critique. One row per
    ModelOutput (upsert by output_id), best-effort. render_path is the captured
    thumbnail; critic_note/dists/dreamsim are populated in Phase 2."""

    __tablename__ = "critique"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), unique=True, index=True)
    render_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    critic_note: Mapped[str] = mapped_column(Text, default="")
    dists: Mapped[float | None] = mapped_column(Float, nullable=True)
    dreamsim: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error
    computed: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ReconTask(Base):
    """Binds a Task to its recon GT-bundle key (species slug). Kept as a separate table
    (not a Task column) so Task stays generic and we avoid an ALTER on the create_all-only
    schema. A Task with a ReconTask row IS a Mode-B recon benchmark; the slug (e.g.
    'arabidopsis_thaliana') is the held-out GT key the scoring service resolves — decoupled
    from bio3d DB PKs so the same private GT is reusable across DB resets (D2)."""

    __tablename__ = "recon_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), unique=True, index=True)
    species_slug: Mapped[str] = mapped_column(String(64), index=True)
    species_name: Mapped[str] = mapped_column(String(128), default="")


class JudgeVote(Base):
    """One VLM-judge judgment for a pair under a perception condition. winner ∈
    {a,b,tie,bad}; output_a_id/output_b_id are the PRESENTED slots, so winner maps
    back to a real output through them. swap_group links the A/B and B/A orders of
    the same logical comparison (judge self-consistency / position bias). Separate
    from human `vote` — the human integrity path never touches this table."""

    __tablename__ = "judge_vote"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    output_a_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)
    output_b_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    winner: Mapped[str] = mapped_column(String(8))  # 'a' | 'b' | 'tie' | 'bad'
    view_condition: Mapped[str] = mapped_column(String(16), index=True)  # single|multi4|turntable
    judge_model: Mapped[str] = mapped_column(String(48))
    swap_group: Mapped[str] = mapped_column(String(64), index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class JudgeRating(Base):
    """VLM-side cached ranking — mirrors `Rating` plus a `view_condition` key so each
    perception condition has its own leaderboard. Kept separate from `Rating` so the
    human leaderboard is never polluted by judge votes."""

    __tablename__ = "judge_rating"
    __table_args__ = (
        UniqueConstraint(
            "generator_id",
            "category_id",
            "criterion_id",
            "view_condition",
            name="uq_judge_rating_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    generator_id: Mapped[int] = mapped_column(ForeignKey("generator.id"), index=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True, index=True
    )
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    view_condition: Mapped[str] = mapped_column(String(16), index=True)
    elo: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_score: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_lower: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_upper: Mapped[float] = mapped_column(Float, default=1000.0)
    n_games: Mapped[int] = mapped_column(Integer, default=0)
    judge_model: Mapped[str] = mapped_column(String(48), default="")
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class CalibrationPair(Base):
    """A pair in the shared calibration subset — voted by BOTH the human (via
    /api/next?set=calibration) and the VLM judge, so agreement (κ) is measured on the
    same pairings. Distinct from GoldPair (which has a known-correct answer)."""

    __tablename__ = "calibration_pair"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "output_a_id",
            "output_b_id",
            "criterion_id",
            name="uq_calibration_pair",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    output_a_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    output_b_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class TaskDifficulty(Base):
    """Manually-curated difficulty tier for a benchmark Task. Separate table (not a
    Task column) to honor the create_all-only schema — mirrors ReconTask/OrganMetric.
    tier ∈ difficulty.TIERS; rationale is free text (why this tier)."""

    __tablename__ = "task_difficulty"
    __table_args__ = (UniqueConstraint("task_id", name="uq_task_difficulty_task"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), unique=True, index=True)
    tier: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text, default="")
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class PlantMorphology(Base):
    """Hand-curated growth-form classification per recon subject. Separate table (not a
    Task/ReconTask column) to honor the create_all-only schema — mirrors TaskDifficulty.
    subject_slug is the CROPS short slug; the per-form recipe lives in morphology.STRATEGY."""

    __tablename__ = "plant_morphology"
    __table_args__ = (UniqueConstraint("subject_slug", name="uq_plant_morphology_subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    growth_form: Mapped[str] = mapped_column(String(32))
    notes: Mapped[str] = mapped_column(Text, default="")
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class TraitRubric(Base):
    """Literature-sourced botanical-trait rubric for one taxon. traits_json is a list of
    {key, trait_class, type, expected, visual, source_tier, citation} (see the Mode-C spec)."""

    __tablename__ = "trait_rubric"

    id: Mapped[int] = mapped_column(primary_key=True)
    taxon: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True, index=True)
    traits_json: Mapped[str] = mapped_column(Text, default="[]")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class TraitVerdict(Base):
    """One VLM verdict for one (output, trait). JudgeVote analog."""

    __tablename__ = "trait_verdict"
    __table_args__ = (
        UniqueConstraint("output_id", "trait_key", "judge_model", name="uq_trait_verdict"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)
    rubric_id: Mapped[int] = mapped_column(ForeignKey("trait_rubric.id"), index=True)
    trait_key: Mapped[str] = mapped_column(String(64))
    trait_class: Mapped[str] = mapped_column(String(32), index=True)
    verdict: Mapped[str] = mapped_column(
        String(20)
    )  # present_correct|present_wrong|absent|not_assessable
    rationale: Mapped[str] = mapped_column(Text, default="")
    judge_model: Mapped[str] = mapped_column(String(64), default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class TraitScore(Base):
    """Per-output botanical-accuracy score (calibrated classes only). Metric analog."""

    __tablename__ = "trait_score"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), unique=True, index=True)
    botanical_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_scored: Mapped[int] = mapped_column(Integer, default=0)
    n_total: Mapped[int] = mapped_column(Integer, default=0)
    judge_model: Mapped[str] = mapped_column(String(64), default="")
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class TraitCalibration(Base):
    """Per-trait-class human↔VLM agreement gate. accepted classes count toward scores."""

    __tablename__ = "trait_calibration"

    id: Mapped[int] = mapped_column(primary_key=True)
    trait_class: Mapped[str] = mapped_column(String(32), unique=True)
    kappa: Mapped[float | None] = mapped_column(Float, nullable=True)
    n: Mapped[int] = mapped_column(Integer, default=0)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ModelScope(Base):
    """What plant parts a model actually depicts (VLM 'scope' pass). is_plant=False marks junk;
    parts_json is a JSON list drawn from scope.SCOPE_PARTS. Consulted by scope.is_assessable so
    a trait is only judged on a model that shows the relevant structure (e.g. habit is skipped
    on a single-fruit model). One row per output per judge_model."""

    __tablename__ = "model_scope"
    __table_args__ = (UniqueConstraint("output_id", "judge_model", name="uq_model_scope"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)
    is_plant: Mapped[bool] = mapped_column(Boolean, default=True)
    parts_json: Mapped[str] = mapped_column(Text, default="[]")
    rationale: Mapped[str] = mapped_column(Text, default="")
    judge_model: Mapped[str] = mapped_column(String(64), default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class CommissionAttempt(Base):
    """One agent's attempt at one task in the commissioned-generation arena. Records the
    script + outcome even on failure (output_id NULL), so execution-success rate is a real
    metric. One row per (model_id, task_id) — resumable."""

    __tablename__ = "commission_attempt"
    __table_args__ = (UniqueConstraint("model_id", "task_id", name="uq_commission_attempt"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(128), index=True)
    generator_id: Mapped[int | None] = mapped_column(ForeignKey("generator.id"), nullable=True)
    output_id: Mapped[int | None] = mapped_column(ForeignKey("model_output.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # ok|error|timeout|invalid_mesh
    error: Mapped[str] = mapped_column(Text, default="")
    script: Mapped[str] = mapped_column(Text, default="")
    mesh_stats_json: Mapped[str] = mapped_column(Text, default="{}")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
