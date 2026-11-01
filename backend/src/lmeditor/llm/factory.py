"""DSPy language-model bootstrap.

This module owns the single global act of loading a language model into DSPy.
Everything else in LMeditor asks :func:`require_lm` (or checks
:func:`is_configured`) instead of touching ``dspy.settings`` directly, so that
there is exactly one place where "is a model available?" is answered.

The design rule this package enforces: **LMeditor never fabricates prose in
place of a model call.** When no model is available, the generative paths
raise :class:`~lmeditor.core.exceptions.LMNotConfiguredError` rather than
substituting template text that a reader could mistake for generated output.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from lmeditor.core.exceptions import LMNotConfiguredError
from lmeditor.llm.config import LMConfig, LMProvider

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_config: Optional[LMConfig] = None


def build_lm(config: LMConfig) -> Any:
    """Construct a ``dspy.LM`` for the given configuration.

    Args:
        config: a validated provider/model configuration.

    Returns:
        A configured ``dspy.LM`` instance (not yet installed globally).
    """
    import dspy

    kwargs: dict[str, Any] = {
        "model": config.dspy_model_string,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    if config.provider is LMProvider.STUB:
        # The offline double is not a litellm model; it is a local class that
        # answers DSPy prompts directly.
        from lmeditor.llm.stub import StubLM

        return StubLM()
    if config.provider is LMProvider.CODEX:
        # The CLI owns credentials. "default" uses its built-in model default.
        from lmeditor.llm.codex import CodexLM

        return CodexLM(model=config.model, max_tokens=config.max_tokens)
    if config.api_key:
        kwargs["api_key"] = config.api_key
    if config.base_url:
        kwargs["api_base"] = config.base_url
    return dspy.LM(**kwargs)


def configure_lm(config: Optional[LMConfig] = None) -> Optional[LMConfig]:
    """Install a language model into DSPy.

    Args:
        config: the configuration to install. When ``None``, the
            configuration is read from the environment.

    Returns:
        The installed configuration, or ``None`` when nothing is configured
        (no provider set) or the install failed. A failure is logged rather
        than raised so the app can still start and report its state through
        ``/api/v1/health``.
    """
    global _active_config

    resolved = config if config is not None else LMConfig.from_env()
    if resolved is None:
        with _lock:
            _active_config = None
        logger.warning(
            "No language model configured: set LMEDITOR_LM_PROVIDER / LMEDITOR_LM_MODEL "
            "to enable drafting. Generative operations will fail loudly."
        )
        return None

    try:
        resolved.validate_ready(operation="startup")
        lm = build_lm(resolved)
        import dspy

        dspy.configure(lm=lm)
    except Exception as exc:  # noqa: BLE001 - startup must not crash the app
        with _lock:
            _active_config = None
        logger.error("Failed to configure the language model: %s", exc)
        return None

    with _lock:
        _active_config = resolved
    logger.info("Language model configured: %s", resolved.describe())
    return resolved


def is_configured() -> bool:
    """Whether a language model is currently loaded."""
    try:
        import dspy

        return dspy.settings.lm is not None
    except Exception:  # noqa: BLE001 - dspy may be unavailable
        return False


def current_config() -> Optional[LMConfig]:
    """The configuration that is currently installed, if any."""
    with _lock:
        return _active_config


def require_lm(operation: str = "generation") -> Any:
    """Return the loaded LM, or raise if none is available.

    Args:
        operation: the operation requiring the model; used in the error.

    Raises:
        LMNotConfiguredError: when no model is loaded.
    """
    try:
        import dspy

        lm = dspy.settings.lm
    except Exception as exc:  # noqa: BLE001 - dspy import failure
        raise LMNotConfiguredError(
            operation, f"The language-model runtime is unavailable: {exc}"
        ) from exc

    if lm is None:
        raise LMNotConfiguredError(operation)
    return lm


def status() -> dict[str, Any]:
    """A machine-readable summary of LM availability for ``/api/v1/health``."""
    config = current_config()
    configured = is_configured()
    model: Optional[str] = config.model if config else None
    provider: Optional[str] = config.provider.value if config else None
    if model is None and configured:
        # A model was installed outside this module (tests install a stub LM);
        # report what can be read without inventing a provider.
        try:
            import dspy

            candidate = getattr(dspy.settings.lm, "model", None)
            if isinstance(candidate, str):
                model = candidate
        except Exception:  # noqa: BLE001
            pass
    is_stub = bool(config and config.provider is LMProvider.STUB)
    if configured and is_stub:
        detail = (
            "The offline stub drafter is loaded. Drafts quote their source "
            "passages verbatim; this is not a language model."
        )
    elif configured and config and config.provider is LMProvider.CODEX:
        detail = (
            "The Codex CLI adapter is loaded; drafting uses its saved sign-in. "
            "CLI authentication and model availability are checked on generation."
        )
    elif configured:
        detail = "A language model is loaded; drafting is enabled."
    else:
        detail = (
            "No language model is configured; drafting is disabled. "
            "Set LMEDITOR_LM_PROVIDER / LMEDITOR_LM_MODEL to enable it."
        )

    return {
        "configured": configured,
        "provider": provider,
        "model": model,
        "base_url": config.base_url if config else None,
        "detail": detail,
        "is_stub": is_stub,
    }


def reset() -> None:
    """Clear the installed model. Intended for tests."""
    global _active_config
    with _lock:
        _active_config = None
    try:
        import dspy

        dspy.configure(lm=None)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "LMProvider",
    "LMConfig",
    "build_lm",
    "configure_lm",
    "current_config",
    "is_configured",
    "require_lm",
    "reset",
    "status",
]
