"""Global pytest fixtures and environment configuration for ProjectBrain test suites.

Ensures all tests use isolated temporary directories for caches and databases,
preventing stray .projectbrain/ or .db artifacts in repository root.
"""

from __future__ import annotations

import os
# Ensure LLM_API_KEY is available so MCP extensions load in headless test environments
os.environ.setdefault("LLM_API_KEY", "test")

import shutil
from pathlib import Path
import pytest

from projectbrain.graphify_engine.extractor import GraphifyExtractor


@pytest.fixture(autouse=True)
def isolated_graphify_cache(tmp_path, monkeypatch):
    """Ensure any GraphifyExtractor instantiated during tests defaults its cache to tmp_path."""
    temp_cache = tmp_path / "pb_test_cache"
    orig_init = GraphifyExtractor.__init__

    def custom_init(self, cache_dir=None, *args, **kwargs):
        orig_init(self, cache_dir=cache_dir or temp_cache, *args, **kwargs)

    monkeypatch.setattr(GraphifyExtractor, "__init__", custom_init)
    yield
    # Clean up any stray temporary cache or local graphs created during test runs
    for stray in [".projectbrain/cache", ".projectbrain/local_graph"]:
        if os.path.exists(stray):
            shutil.rmtree(stray, ignore_errors=True)
