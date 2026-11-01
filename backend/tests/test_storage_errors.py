"""Storage refusals must keep their identity and context across the bridge."""

from __future__ import annotations

import pytest

from lmeditor.bridge.client import _rehydrate
from lmeditor.core.exceptions import (
    IncompleteOperationError,
    LMeditorError,
    RevisionConflictError,
    StorageWriteError,
)


@pytest.mark.parametrize(
    "error",
    [
        StorageWriteError("/archive/source.json", "disk_full"),
        RevisionConflictError("source-1", 2, 3, "PreconditionMismatch"),
        IncompleteOperationError("operation-1", "rename", ["before.md", "after.md"]),
    ],
)
def test_storage_error_roundtrip_preserves_domain_type_and_details(error: LMeditorError) -> None:
    restored = _rehydrate(type(error).__name__, error.message, error.details)
    assert type(restored) is type(error)
    assert isinstance(restored, LMeditorError)
    assert restored.details == error.details
    assert restored.message == error.message
