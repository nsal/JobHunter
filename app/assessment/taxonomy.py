"""Fixed, versioned taxonomy for deterministic assessment scoring."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from app.ai.schema_models import CategoryAlignment, SupportingCategory


@dataclass(frozen=True)
class AssessmentTaxonomy:
    """One immutable scoring taxonomy published by application code."""

    version: str
    category_weights: Mapping[SupportingCategory, Decimal]
    alignment_scores: Mapping[CategoryAlignment, Decimal]

    def __post_init__(self) -> None:
        if sum(self.category_weights.values()) != Decimal(100):
            raise ValueError("supporting category weights must total 100")
        if set(self.category_weights) != set(SupportingCategory):
            raise ValueError("taxonomy must weight every supporting category")
        if set(self.alignment_scores) != set(CategoryAlignment):
            raise ValueError("taxonomy must score every category alignment")

    def as_document(self) -> dict[str, object]:
        """Return the stable public document used for hashing and artefacts."""
        return {
            "version": self.version,
            "category_weights": {
                category.value: int(self.category_weights[category])
                for category in SupportingCategory
            },
            "alignment_scores": {
                alignment.value: int(self.alignment_scores[alignment])
                for alignment in CategoryAlignment
            },
        }

    @property
    def sha256(self) -> str:
        """Return a content hash for the complete taxonomy."""
        encoded = json.dumps(
            self.as_document(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


V1_TAXONOMY: Final = AssessmentTaxonomy(
    version="v1",
    category_weights=MappingProxyType(
        {
            SupportingCategory.CORE_SKILLS: Decimal(30),
            SupportingCategory.RELEVANT_EXPERIENCE: Decimal(25),
            SupportingCategory.DOMAIN_KNOWLEDGE: Decimal(15),
            SupportingCategory.RESPONSIBILITIES: Decimal(20),
            SupportingCategory.QUALIFICATIONS: Decimal(10),
        }
    ),
    alignment_scores=MappingProxyType(
        {
            CategoryAlignment.STRONG: Decimal(100),
            CategoryAlignment.MATCHED: Decimal(80),
            CategoryAlignment.PARTIAL: Decimal(50),
            CategoryAlignment.GAP: Decimal(0),
            CategoryAlignment.UNKNOWN: Decimal(0),
            CategoryAlignment.NOT_APPLICABLE: Decimal(100),
        }
    ),
)

TAXONOMIES: Final = MappingProxyType({V1_TAXONOMY.version: V1_TAXONOMY})


def get_taxonomy(version: str) -> AssessmentTaxonomy:
    """Resolve a supported taxonomy version without silently falling back."""
    try:
        return TAXONOMIES[version]
    except KeyError as error:
        raise ValueError(
            f"Unsupported assessment taxonomy: {version}"
        ) from error
