from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2] / "src" / "projectbrain"

_modified_keys = [
    "projectbrain",
    "projectbrain.utils",
    "projectbrain.memory",
    "projectbrain.core",
    "projectbrain.ops",
    "projectbrain.utils.text",
    "projectbrain.core.db",
    "projectbrain.core.config",
    "projectbrain.core.constants",
    "projectbrain.core.vector_store",
    "projectbrain.utils.chunking",
    "projectbrain.utils.keyword",
    "projectbrain.utils.vectors",
    "projectbrain.memory.embed",
    "projectbrain.memory.decay",
    "projectbrain.ops.dynamics",
    "projectbrain.memory.user_summary",
    "projectbrain.memory.hsg",
]
_original_modules = {k: sys.modules[k] for k in _modified_keys if k in sys.modules}


def _ensure_pkg(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        mod.__path__ = []  # type: ignore[attr-defined]
        sys.modules[name] = mod
    return mod


def _stub_module(name: str, **attrs: object) -> None:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_text_and_hsg():
    _ensure_pkg("projectbrain")
    _ensure_pkg("projectbrain.utils")
    _ensure_pkg("projectbrain.memory")
    _ensure_pkg("projectbrain.core")
    _ensure_pkg("projectbrain.ops")

    text = _load_module("projectbrain.utils.text", ROOT / "utils" / "text.py")

    _stub_module("projectbrain.core.db", q=None, db=None, transaction=lambda: None)
    _stub_module("projectbrain.core.config", env=types.SimpleNamespace())
    _stub_module("projectbrain.core.constants", SECTOR_CONFIGS={})
    _stub_module("projectbrain.core.vector_store", vector_store=None)
    _stub_module("projectbrain.utils.chunking", chunk_text=lambda *args, **kwargs: [])
    _stub_module(
        "projectbrain.utils.keyword",
        keyword_filter_memories=lambda *args, **kwargs: [],
        compute_keyword_overlap=lambda *args, **kwargs: 0.0,
    )
    _stub_module(
        "projectbrain.utils.vectors",
        buf_to_vec=lambda *args, **kwargs: [],
        vec_to_buf=lambda *args, **kwargs: b"",
        cos_sim=lambda *args, **kwargs: 0.0,
    )
    _stub_module(
        "projectbrain.memory.embed",
        embed_multi_sector=lambda *args, **kwargs: {},
        embed_for_sector=lambda *args, **kwargs: [],
        calc_mean_vec=lambda *args, **kwargs: [],
    )
    _stub_module(
        "projectbrain.memory.decay",
        inc_q=lambda *args, **kwargs: None,
        dec_q=lambda *args, **kwargs: None,
        on_query_hit=lambda *args, **kwargs: None,
        calc_recency_score=lambda *args, **kwargs: 0.0,
        pick_tier=lambda *args, **kwargs: "cold",
    )
    _stub_module(
        "projectbrain.ops.dynamics",
        calculateCrossSectorResonanceScore=lambda *args, **kwargs: 0.0,
        applyRetrievalTraceReinforcementToMemory=lambda *args, **kwargs: None,
        propagateAssociativeReinforcementToLinkedNodes=lambda *args, **kwargs: None,
    )
    _stub_module("projectbrain.memory.user_summary", update_user_summary=lambda *args, **kwargs: None)

    hsg = _load_module("projectbrain.memory.hsg", ROOT / "memory" / "hsg.py")
    return text, hsg


TEXT, HSG = _load_text_and_hsg()

for k in _modified_keys:
    if k in _original_modules:
        sys.modules[k] = _original_modules[k]
    elif k in sys.modules:
        del sys.modules[k]




def test_tokenize_expands_cjk_bigrams():
    assert TEXT.tokenize("我喜欢普洱茶") == ["我喜", "喜欢", "欢普", "普洱", "洱茶"]


def test_canonical_tokens_keep_distinct_chinese_content():
    left = TEXT.canonical_tokens_from_text("我喜欢健身")
    right = TEXT.canonical_tokens_from_text("我喜欢普洱茶")

    assert left
    assert right
    assert set(left) != set(right)


def test_compute_simhash_avoids_constant_hash_for_distinct_chinese_inputs():
    left = HSG.compute_simhash("我喜欢健身")
    right = HSG.compute_simhash("我喜欢普洱茶")

    assert left != right


def test_compute_simhash_uses_stable_fallback_when_tokenizer_finds_nothing():
    left = HSG.compute_simhash("!!!")
    right = HSG.compute_simhash("???")

    assert left != right
    assert left == HSG.compute_simhash("!!!")
