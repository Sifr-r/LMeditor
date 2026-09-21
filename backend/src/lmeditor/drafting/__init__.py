"""DSPy declarative drafting package with teleprompter optimization and runtime assertions."""

from __future__ import annotations

from lmeditor.drafting.modules import MacroCohesionModule, SectionDrafter
from lmeditor.drafting.signatures import (
    CitationInsertionSignature,
    MacroCohesionSignature,
    SectionDraftingSignature,
)
from lmeditor.drafting.teleprompter import (
    DraftingTeleprompterCompiler,
    citation_precision_metric,
)

__all__ = [
    "CitationInsertionSignature",
    "DraftingTeleprompterCompiler",
    "MacroCohesionModule",
    "MacroCohesionSignature",
    "SectionDrafter",
    "SectionDraftingSignature",
    "citation_precision_metric",
]
