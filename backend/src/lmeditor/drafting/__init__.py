"""DSPy declarative drafting package with teleprompter optimization and runtime assertions."""

from __future__ import annotations

from lmeditor.drafting.modules import CitationInserter, MacroCohesionModule, SectionDrafter
from lmeditor.drafting.signatures import (
    CitationInsertionSignature,
    MacroCohesionSignature,
    SectionDraftingSignature,
)
from lmeditor.drafting.teleprompter import (
    OPTIMIZER_AUTO,
    OPTIMIZER_BOOTSTRAP_FEW_SHOT,
    OPTIMIZER_MIPRO_V2,
    DraftingTeleprompterCompiler,
    citation_precision_metric,
)

__all__ = [
    "OPTIMIZER_AUTO",
    "OPTIMIZER_BOOTSTRAP_FEW_SHOT",
    "OPTIMIZER_MIPRO_V2",
    "CitationInsertionSignature",
    "CitationInserter",
    "DraftingTeleprompterCompiler",
    "MacroCohesionModule",
    "MacroCohesionSignature",
    "SectionDrafter",
    "SectionDraftingSignature",
    "citation_precision_metric",
]
