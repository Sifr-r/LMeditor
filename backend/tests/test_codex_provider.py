"""Offline proof of the authenticated CLI boundary used for real generation."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lmeditor.core.exceptions import LMNotConfiguredError
from lmeditor.llm import LMConfig, LMProvider, configure_lm, reset, status
from lmeditor.llm import codex
from lmeditor.llm.config import parse_provider


@pytest.fixture(autouse=True)
def clean_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset()
    for name in ("LMEDITOR_CODEX_EXECUTABLE", "LMEDITOR_CODEX_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(codex.shutil, "which", lambda _name: sys.executable)
    yield
    reset()


def successful_run(
    monkeypatch: pytest.MonkeyPatch, text: str = "[[ ## answer ## ]]\nGenerated answer."
) -> list[tuple[list[str], dict[str, Any]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        Path(args[args.index("--output-last-message") + 1]).write_text(text, encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(codex.subprocess, "run", run)
    return calls


def test_codex_configuration_uses_cli_authentication_and_default_model() -> None:
    config = LMConfig.from_env({"LMEDITOR_LM_PROVIDER": "codex"})
    assert config is not None
    assert config.provider is LMProvider.CODEX
    assert config.model == "default"
    assert config.api_key is None
    assert config.base_url is None
    assert not config.provider.requires_api_key
    config.validate_ready("drafting")
    assert parse_provider(" CODEX-CLI ") is LMProvider.CODEX


def test_codex_bootstrap_reports_real_provider_without_generating() -> None:
    config = LMConfig(provider=LMProvider.CODEX, model="default")
    assert configure_lm(config) == config
    report = status()
    assert report["configured"] is True
    assert report["provider"] == "codex"
    assert report["is_stub"] is False


@pytest.mark.parametrize("settings", [{"api_key": "unused-secret"}, {"base_url": "https://example.com"}])
def test_codex_refuses_unused_http_credentials_and_endpoints(settings: dict[str, str]) -> None:
    config = LMConfig(provider=LMProvider.CODEX, model="default", **settings)
    with pytest.raises(LMNotConfiguredError, match="saved authentication"):
        config.validate_ready("drafting")


def test_codex_preserves_dspy_fields_over_stdin_without_shell_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = successful_run(monkeypatch)
    result = codex.CodexLM().forward(messages=[
        {"role": "system", "content": "Return [[ ## answer ## ]] and [[ ## completed ## ]]."},
        {"role": "user", "content": "[[ ## question ## ]]\nA source passage [#abc123]."},
    ])
    args, options = calls[0]
    assert args[0] == sys.executable
    assert "exec" in args
    assert "--ephemeral" in args
    assert "--ignore-user-config" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--model" not in args
    assert args[-1] == "-"
    assert not options.get("shell", False)
    assert "Return [[ ## answer ## ]]" in options["input"]
    assert "A source passage [#abc123]." in options["input"]
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    assert options["timeout"] == 180
    assert Path(options["cwd"]).resolve().is_relative_to(Path(__file__).resolve().parents[2])
    assert result.choices[0].message.content.splitlines() == ["[[ ## answer ## ]]", "Generated answer."]


def test_explicit_model_is_one_argument_and_cannot_inject_shell_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = successful_run(monkeypatch)
    codex.CodexLM(model="explicit-model").forward(prompt="Write a sentence.")
    args, _options = calls[0]
    assert args[args.index("--model") + 1] == "explicit-model"


def test_codex_public_dspy_call_returns_generated_text(monkeypatch: pytest.MonkeyPatch) -> None:
    successful_run(monkeypatch, "A generated response.")
    assert codex.CodexLM()("Write a sentence.") == ["A generated response."]


def test_nonzero_exit_is_contextual_and_does_not_echo_private_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex.subprocess, "run", lambda args, **_kwargs: subprocess.CompletedProcess(args, 1))
    with pytest.raises(codex.CodexGenerationError) as excinfo:
        codex.CodexLM().forward(prompt="Private source content should not appear in errors.")
    assert "Codex" in str(excinfo.value)
    assert "Private source content" not in str(excinfo.value)


def test_timeout_is_contextual_without_echoing_subprocess_input(monkeypatch: pytest.MonkeyPatch) -> None:
    def timed_out(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 180, output="private output", stderr="private token")

    monkeypatch.setattr(codex.subprocess, "run", timed_out)
    with pytest.raises(codex.CodexGenerationError) as excinfo:
        codex.CodexLM().forward(prompt="Private source")
    assert "180" in str(excinfo.value)
    assert "private output" not in str(excinfo.value)
    assert "private token" not in str(excinfo.value)


@pytest.mark.parametrize("output", ["", " \n\t"])
def test_empty_cli_output_is_a_failure(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    successful_run(monkeypatch, output)
    with pytest.raises(codex.CodexGenerationError):
        codex.CodexLM().forward(prompt="Write a sentence.")


def test_missing_cli_output_file_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex.subprocess, "run", lambda args, **_kwargs: subprocess.CompletedProcess(args, 0))
    with pytest.raises(codex.CodexGenerationError):
        codex.CodexLM().forward(prompt="Write a sentence.")


def test_missing_executable_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex.shutil, "which", lambda _name: None)
    with pytest.raises(codex.CodexGenerationError):
        codex.CodexLM()


@pytest.mark.parametrize("prompt", ["", " \n", None])
def test_absent_prompt_is_refused_before_starting_cli(
    monkeypatch: pytest.MonkeyPatch, prompt: str | None,
) -> None:
    calls = successful_run(monkeypatch)
    with pytest.raises((ValueError, codex.CodexGenerationError)):
        codex.CodexLM().forward(prompt=prompt)
    assert calls == []


def test_multiple_completions_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = successful_run(monkeypatch)
    with pytest.raises((ValueError, codex.CodexGenerationError)):
        codex.CodexLM().forward(prompt="Write.", n=2)
    assert calls == []


def test_nontext_message_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = successful_run(monkeypatch)
    with pytest.raises((ValueError, codex.CodexGenerationError)):
        codex.CodexLM().forward(messages=[{"role": "user", "content": [{"type": "image"}]}])
    assert calls == []


@pytest.mark.parametrize("model", ["", "--dangerously-bypass-approvals-and-sandbox", "model\n--flag"])
def test_invalid_model_cannot_become_a_cli_option(model: str) -> None:
    with pytest.raises(codex.CodexGenerationError, match="model"):
        codex.CodexLM(model=model)


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "601", "not-a-number"])
def test_timeout_configuration_is_bounded(monkeypatch: pytest.MonkeyPatch, timeout: str) -> None:
    monkeypatch.setenv("LMEDITOR_CODEX_TIMEOUT_SECONDS", timeout)
    with pytest.raises(codex.CodexGenerationError, match="TIMEOUT"):
        codex.CodexLM()


def test_oversized_output_fails_instead_of_truncating(monkeypatch: pytest.MonkeyPatch) -> None:
    successful_run(monkeypatch, "x" * (1_048_576 + 1))
    with pytest.raises(codex.CodexGenerationError, match="response exceeds"):
        codex.CodexLM().forward(prompt="Write a sentence.")


def test_oversized_input_never_starts_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = successful_run(monkeypatch)
    with pytest.raises(codex.CodexGenerationError, match="input exceeds"):
        codex.CodexLM().forward(prompt="x" * (1_048_576 + 1))
    assert calls == []


@pytest.mark.asyncio
async def test_async_forward_returns_the_same_provider_response(monkeypatch: pytest.MonkeyPatch) -> None:
    successful_run(monkeypatch, "Async response.")
    result = await codex.CodexLM().aforward(prompt="Write a sentence.")
    assert result.choices[0].message.content == "Async response."
