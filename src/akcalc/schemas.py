"""Pydantic models shared by the knowledge base, agent and evaluation."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(str, Enum):
    FORMULA_LOOKUP = "formula_lookup"
    CALCULATE = "calculate"
    ENTITY_LOOKUP = "entity_lookup"
    CLARIFY = "clarify"
    REFUSE = "refuse"


class Citation(BaseModel):
    source_id: str
    title: str
    url: str
    section: str = ""
    retrieved_at: str = ""
    quote: str = ""


class VariableSpec(BaseModel):
    symbol: str
    name: str
    unit: str = ""
    description: str = ""
    required: bool = True
    min_value: float | None = None
    max_value: float | None = None


class FormulaCard(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    damage_type: Literal["physical", "arts", "true", "healing", "utility"] = "physical"
    expression: str
    variables: list[VariableSpec] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    notes: str = ""
    citation: Citation
    examples: list[str] = Field(default_factory=list)
    version: str = "2026-11-04"


class DamageComponent(BaseModel):
    """An extra damage instance that rides on each hit of a skill.

    Example: 麒麟R夜刀's first talent adds arts damage to every attack, and her
    S2 multiplies that talent by 3.75, giving a second 75%-ATK arts component
    alongside each 150%-ATK physical hit.
    """

    damage_type: Literal["physical", "arts", "true"] = "arts"
    atk_multiplier: float = 0.0
    hits: int = 1
    label: str = ""


class SkillRecord(BaseModel):
    id: str
    operator_id: str
    name: str
    skill_index: int = 3
    description: str = ""
    atk_bonus: float = 0.0
    atk_multiplier: float = 1.0
    heal_multiplier: float = 1.0
    atk_time_flat: float = 0.0
    aspd_flat: float = 0.0
    damage_type: Literal["physical", "arts", "true", "healing", "utility"] = "physical"
    hits: int = 1
    duration: float | None = None
    extra_components: list[DamageComponent] = Field(default_factory=list)
    trait_up: float = 1.0
    per_kill_reduce: float = 0.0
    formula_note: str = ""
    calc_supported: bool = True
    unsupported_reason: str = ""
    calc_note: str = ""
    citation: Citation | None = None


class OperatorRecord(BaseModel):
    id: str
    name: str
    class_name: str = ""
    sub_profession: str = ""
    trait: str = ""
    rarity: int = 6
    phase: int = 2
    level: int = 90
    atk: float
    def_value: float = 0
    res_value: float = 0
    hp: float = 0
    attack_interval: float = 1.0
    trait_atk_bonus: float = 0.0
    trait_charge_time: float = 0.0
    talent_atk_scale: float = 1.0
    skills: list[SkillRecord] = Field(default_factory=list)
    citation: Citation | None = None


class EntityLink(BaseModel):
    mention: str
    canonical_id: str
    kind: Literal["operator", "skill", "formula", "enemy", "unknown"] = "unknown"
    confidence: float = 0.0


class Plan(BaseModel):
    intent: Intent = Intent.CLARIFY
    formula_id: str | None = None
    entity_links: list[EntityLink] = Field(default_factory=list)
    variable_bindings: dict[str, float] = Field(default_factory=dict)
    missing_vars: list[str] = Field(default_factory=list)
    refusal_reason: str = ""
    confidence: float = 0.0
    reasoning: str = ""


class CalculationStep(BaseModel):
    label: str
    expression: str
    value: float | str | None = None
    note: str = ""


class CalculationResult(BaseModel):
    formula_id: str
    formula_name: str = ""
    expression: str = ""
    inputs: dict[str, float] = Field(default_factory=dict)
    steps: list[CalculationStep] = Field(default_factory=list)
    raw_value: float
    display_value: float
    unit: str = ""
    warnings: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class Answer(BaseModel):
    query: str
    answer_text: str
    plan: Plan
    calculation: CalculationResult | None = None
    citations: list[Citation] = Field(default_factory=list)
    refused: bool = False
    refusal_reason: str = ""
    confidence: float = 0.0
    trace_id: str = ""
    latency_ms: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)


class RetrievedDoc(BaseModel):
    doc_id: str
    kind: Literal["formula", "operator", "skill", "note"] = "formula"
    title: str
    text: str
    score: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)


class EvalQuestion(BaseModel):
    id: str
    question: str
    category: Literal[
        "concept", "calculation", "skill_buff", "attack_speed", "refusal", "clarify"
    ]
    gold_answer: str
    gold_value: float | None = None
    tolerance: float = 1.0
    formula_ids: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    refusal_expected: bool = False
    clarify_expected: bool = False
    notes: str = ""


class EvalRecord(BaseModel):
    question_id: str
    system: str
    prediction: str
    predicted_value: float | None = None
    gold_value: float | None = None
    numeric_correct: bool | None = None
    formula_correct: bool | None = None
    citation_correct: bool | None = None
    refusal_correct: bool | None = None
    clarify_correct: bool | None = None
    hallucination: bool = False
    latency_ms: float = 0.0
    error: str = ""


class EvalSummary(BaseModel):
    system: str
    n: int
    numeric_accuracy: float
    formula_accuracy: float
    citation_accuracy: float
    refusal_accuracy: float
    clarify_accuracy: float
    hallucination_rate: float
    avg_latency_ms: float
