"""Tests for the language-model configuration and bootstrap.

The harness could not write at all before this layer existed: the drafting
modules were real DSPy programs, but nothing ever loaded a model, so every
call raised "No LM is loaded" and the workflow substituted template prose.
These tests pin the configuration contract and the failure behaviour.
"""

from __future__ import annotations

import pytest

from lmeditor.core.exceptions import LMNotConfiguredError
from lmeditor.llm import (
    LMConfig,
    LMProvider,
    StubLM,
    configure_lm,
    current_config,
    is_configured,
    require_lm,
    reset,
    status,
)
from lmeditor.llm.config import parse_provider


@pytest.fixture(autouse=True)
def _clean_model() -> None:
    """Leave the global model configuration clean around each test."""
    reset()
    yield
    reset()


def test_no_configuration_means_no_model() -> None:
    """With nothing configured, the harness reports that it cannot write."""
    assert LMConfig.from_env({}) is None
    assert is_configured() is False
    assert status()["configured"] is False
    assert "disabled" in status()["detail"]


def test_a_provider_and_model_are_read_from_the_environment() -> None:
    """The environment contract is the whole configuration surface."""
    config = LMConfig.from_env(
        {
            "LMEDITOR_LM_PROVIDER": "openai",
            "LMEDITOR_LM_MODEL": "gpt-4o-mini",
            "LMEDITOR_LM_API_KEY": "sk-test",
            "LMEDITOR_LM_TEMPERATURE": "0.7",
            "LMEDITOR_LM_MAX_TOKENS": "512",
        }
    )
    assert config is not None
    assert config.provider is LMProvider.OPENAI
    assert config.model == "gpt-4o-mini"
    assert config.api_key == "sk-test"
    assert config.temperature == 0.7
    assert config.max_tokens == 512
    assert config.dspy_model_string == "openai/gpt-4o-mini"


def test_a_local_provider_needs_no_api_key() -> None:
    """Ollama and compatible servers run without credentials."""
    config = LMConfig.from_env(
        {"LMEDITOR_LM_PROVIDER": "ollama", "LMEDITOR_LM_MODEL": "llama3.1"}
    )
    assert config is not None
    config.validate_ready(operation="drafting")
    assert config.base_url == "http://127.0.0.1:11434"
    assert config.dspy_model_string == "ollama/llama3.1"


def test_a_hosted_provider_without_a_key_is_refused() -> None:
    """A missing key is a configuration error, not a silent failure later."""
    config = LMConfig.from_env(
        {"LMEDITOR_LM_PROVIDER": "anthropic", "LMEDITOR_LM_MODEL": "claude-sonnet-4-5"}
    )
    assert config is not None
    with pytest.raises(LMNotConfiguredError) as excinfo:
        config.validate_ready(operation="drafting")
    assert "API key" in str(excinfo.value)


def test_an_unknown_provider_is_refused_by_name() -> None:
    """A typo in the environment must not fall back to some other provider."""
    with pytest.raises(ValueError) as excinfo:
        parse_provider("openai-compatable")
    assert "openai-compatible" in str(excinfo.value)


def test_the_stub_provider_loads_and_says_so() -> None:
    """The offline double is available and identifies itself honestly."""
    config = LMConfig.from_env({"LMEDITOR_LM_PROVIDER": "stub"})
    assert config is not None

    assert configure_lm(config) is not None
    assert is_configured() is True
    assert status()["configured"] is True
    assert status()["is_stub"] is True
    assert "not a language model" in status()["detail"]
    assert current_config() is not None


def test_requiring_a_model_without_one_fails_with_a_clear_message() -> None:
    """Generative paths get an actionable error, not a DSPy stack trace."""
    with pytest.raises(LMNotConfiguredError) as excinfo:
        require_lm("drafting")
    message = str(excinfo.value)
    assert "LMEDITOR_LM_PROVIDER" in message
    assert "drafting" in message


def test_the_stub_drafter_cites_the_passage_it_was_given() -> None:
    """The offline double grounds its draft in a real chunk id."""
    from lmeditor.llm.stub import parse_prompt_fields

    prompt = (
        "[[ ## topic ## ]]\nMunicipal waste\n\n"
        "[[ ## assigned_chunks_text ## ]]\n"
        "--- PASSAGE 1 [#CHUNK_ID: abc123] ---\n"
        "Waste collection fell by 30 percent.\n\n\n"
        "Respond with the corresponding output fields."
    )
    fields = parse_prompt_fields(prompt)
    model = StubLM()
    draft, cited = model._section_draft(fields)

    assert "Waste collection fell by 30 percent." in draft
    assert "[#abc123]" in draft
    assert cited == ["abc123"]
