"""Source-grounded discovery engine for multi-perspective pre-writing research."""

from lmeditor.discovery.dialogue import (
    DialogueLoop,
    conduct_all_dialogues,
    conduct_dialogue,
)
from lmeditor.discovery.outliner import (
    OutlineGenerator,
    generate_outline,
)
from lmeditor.discovery.perspective import (
    PerspectiveDiscoverySignature,
    PerspectiveGenerator,
    generate_perspectives,
)
from lmeditor.discovery.source_analysis import (
    detect_headings,
    extract_findings,
    salient_terms,
)

__all__ = [
    "DialogueLoop",
    "OutlineGenerator",
    "PerspectiveDiscoverySignature",
    "PerspectiveGenerator",
    "conduct_all_dialogues",
    "conduct_dialogue",
    "detect_headings",
    "extract_findings",
    "generate_outline",
    "generate_perspectives",
    "salient_terms",
]
