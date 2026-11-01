"""Language-model configuration and bootstrap for LMeditor.

See :mod:`lmeditor.llm.config` for the environment contract and
:mod:`lmeditor.llm.factory` for the DSPy bootstrap.
"""

from lmeditor.llm.config import LMConfig, LMProvider, parse_provider
from lmeditor.llm.factory import (
    build_lm,
    configure_lm,
    current_config,
    is_configured,
    require_lm,
    reset,
    status,
)
from lmeditor.llm.stub import StubLM

__all__ = [
    "LMConfig",
    "LMProvider",
    "StubLM",
    "parse_provider",
    "build_lm",
    "configure_lm",
    "current_config",
    "is_configured",
    "require_lm",
    "reset",
    "status",
]
