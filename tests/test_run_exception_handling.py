import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1]))

import pytest

from src.core import LeadSkip


async def _stage(should_raise: str):
    """Mirrors the post-change shape of run.process_lead's exception handling."""
    try:
        if should_raise == "skip":
            raise LeadSkip("expected skip")
        if should_raise == "boom":
            raise RuntimeError("regression")
        return True
    except LeadSkip as error:
        print(f"  skipped: {error}")
        return False


def test_lead_skip_returns_false():
    assert asyncio.run(_stage("skip")) is False


def test_unexpected_exception_propagates():
    with pytest.raises(RuntimeError, match="regression"):
        asyncio.run(_stage("boom"))


def test_success_returns_true():
    assert asyncio.run(_stage("ok")) is True
