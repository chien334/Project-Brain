"""Graphify GPU/MPS/CUDA Accelerator & GPU/Local-AI Batch Embedding Engine.

Provides hardware-accelerated graph analytics and batch text embedding for
Project-Brain's Graphify engine and memory subsystem:
- Hardware & library detection reusing ``ComputeDeviceManager`` (CUDA, cuGraph,
  Apple Silicon MPS, PyTorch, sentence-transformers, ONNX Runtime, igraph,
  SciPy, NetworkX, graspologic, NumPy).
- Backend resolution cascade:
  ``cugraph_cuda`` -> ``torch_cuda`` -> ``torch_mps`` -> ``numpy_blas_cpu``.
- Chunked GPU/MPS/BLAS top-k cosine similarity with automatic GPU -> CPU fallback.
- Batch text embedding across ``sentence_transformers`` (CUDA/MPS/CPU),
  Ollama (``/api/embed`` & ``/api/embeddings``), OpenAI-compatible local AI
  servers (vLLM, LM Studio, llama.cpp ``/v1/embeddings``), and a zero-install
  deterministic n-gram TF-IDF projection fallback (<5ms).
- Accelerated PageRank & centrality (cuGraph -> PyTorch sparse GPU -> vectorized NumPy).
- Accelerated community clustering (cuGraph -> igraph Leiden -> graspologic ->
  PyTorch GPU parallel modularity clustering -> NetworkX Louvain) with oversized
  cluster splitting (>25%, min 10 nodes) and cohesion scoring.
"""

from __future__ import annotations

import gc
import importlib.util
import logging
import math
import os
import re
import sys
import threading
import time
import zlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
import numpy as np

from ..core.config import env

logger = logging.getLogger(__name__)

_LOCAL_OPENAI_DEFAULT_URLS: Dict[str, str] = {
    "vllm": "http://localhost:8000/v1",
    "lmstudio": "http://localhost:1234/v1",
    "llamacpp": "http://localhost:8081/v1",
}

_TFIDF_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _module_available(name: str) -> bool:
    """Return True if *name* is imported or discoverable without importing it."""
    if name in sys.modules:
        return sys.modules[name] is not None
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _normalize_openai_base_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.endswith("/v1"):
        return cleaned
    return f"{cleaned}/v1"


def _clear_torch_gpu_cache() -> None:
    """Release Python tensor references and clear CUDA/MPS allocator caches on GPU error."""
    gc.collect()
    try:
        import torch  # type: ignore

        if hasattr(torch, "cuda") and hasattr(torch.cuda, "empty_cache"):
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
        if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
            try:
                torch.mps.empty_cache()
            except Exception:
                pass
    except Exception:
        pass


def _l2_normalize_rows(arr: np.ndarray) -> np.ndarray:
    """L2-normalize rows of a 2D float32 array while preserving zero-norm rows."""
    if arr.size == 0:
        return arr.astype(np.float32, copy=False)
    arr_f = np.asarray(arr, dtype=np.float32)
    norms = np.linalg.norm(arr_f, axis=1, keepdims=True)
    valid = norms > 1e-12
    safe_norms = np.where(valid, norms, 1.0)
    return np.where(valid, arr_f / safe_norms, np.float32(0.0)).astype(
        np.float32, copy=False
    )


def _to_2d_float32(matrix: Any) -> np.ndarray:
    """Convert list/ndarray/Tensor input into a contiguous 2D float32 numpy array."""
    if hasattr(matrix, "detach") and hasattr(matrix, "cpu"):
        matrix = matrix.detach().cpu().numpy()
    arr = np.asarray(matrix, dtype=np.float32)
    if arr.ndim == 1:
        if arr.size == 0:
            return np.zeros((0, 0), dtype=np.float32)
        arr = arr.reshape(1, -1)
    elif arr.ndim != 2:
        raise ValueError(f"Expected 1D or 2D matrix, got {arr.ndim}D")
    return np.ascontiguousarray(arr, dtype=np.float32)


def _detect_compute_hardware() -> Dict[str, Any]:
    """Probe hardware via ComputeDeviceManager (with safe standalone fallback)."""
    base: Dict[str, Any] = {}
    try:
        from ..ocr_runtime import ComputeDeviceManager

        base = dict(ComputeDeviceManager.detect_hardware())
    except Exception:
        base = {
            "os": os.uname().sysname.lower() if hasattr(os, "uname") else sys.platform,
            "cpu_count": os.cpu_count() or 1,
            "cuda_available": False,
            "cuda_device_count": 0,
            "torch_cuda": False,
            "torch_mps": False,
            "mps_available": False,
            "apple_silicon": False,
            "gpu_names": [],
            "onnxruntime_available": _module_available("onnxruntime"),
            "onnx_providers": [],
        }

    torch_available = _module_available("torch")
    torch_cuda = bool(base.get("torch_cuda", False))
    torch_mps = bool(base.get("torch_mps", False))
    if torch_available and ("torch_cuda" not in base or "torch_mps" not in base):
        try:
            import torch  # type: ignore

            torch_cuda = bool(hasattr(torch, "cuda") and torch.cuda.is_available())
            torch_mps = bool(
                hasattr(torch, "backends")
                and hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available()
            )
        except Exception:
            pass

    cugraph_available = _module_available("cugraph")
    sentence_transformers_available = _module_available("sentence_transformers")
    igraph_available = _module_available("igraph")
    scipy_available = _module_available("scipy")
    networkx_available = _module_available("networkx")
    graspologic_available = _module_available("graspologic")
    onnxruntime_available = bool(
        base.get("onnxruntime_available", _module_available("onnxruntime"))
    )

    base.update(
        {
            "torch_available": torch_available,
            "torch_cuda": torch_cuda,
            "torch_mps": torch_mps,
            "mps_available": bool(base.get("mps_available", torch_mps)) and torch_mps,
            "cuda_available": bool(base.get("cuda_available", False)) or torch_cuda,
            "cugraph_available": cugraph_available,
            "sentence_transformers_available": sentence_transformers_available,
            "onnxruntime_available": onnxruntime_available,
            "igraph_available": igraph_available,
            "scipy_available": scipy_available,
            "networkx_available": networkx_available,
            "graspologic_available": graspologic_available,
            "numpy_available": True,
            "numpy_version": getattr(np, "__version__", "unknown"),
        }
    )
    return base


def _parse_device_request(requested: Optional[str]) -> Tuple[str, int]:
    """Normalize a requested device string into (base_device, gpu_index)."""
    raw = str(requested if requested is not None else getattr(env, "graphify_device", "auto"))
    norm = raw.strip().lower() or "auto"
    gpu_id = 0
    base = norm
    if ":" in norm:
        prefix, suffix = norm.split(":", 1)
        base = prefix.strip() or "auto"
        try:
            gpu_id = max(0, int(suffix.strip()))
        except ValueError:
            gpu_id = 0
    return base, gpu_id


def _normalize_edges(
    node_ids: Sequence[str],
    edges: Sequence[Tuple[Any, ...]],
) -> Tuple[Dict[str, int], np.ndarray, np.ndarray, np.ndarray]:
    """Map node IDs and edge tuples to aligned uint/int index and float64 weight arrays."""
    idx_map = {str(nid): i for i, nid in enumerate(node_ids)}
    srcs: List[int] = []
    dsts: List[int] = []
    wts: List[float] = []
    for edge in edges:
        if len(edge) < 2:
            continue
        u = idx_map.get(str(edge[0]))
        v = idx_map.get(str(edge[1]))
        if u is None or v is None:
            continue
        w = float(edge[2]) if len(edge) >= 3 and edge[2] is not None else 1.0
        if not math.isfinite(w) or w <= 0.0:
            continue
        srcs.append(u)
        dsts.append(v)
        wts.append(w)
    if not srcs:
        return (
            idx_map,
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.float64),
        )
    return (
        idx_map,
        np.asarray(srcs, dtype=np.int64),
        np.asarray(dsts, dtype=np.int64),
        np.asarray(wts, dtype=np.float64),
    )


def _compute_cohesion_and_modularity(
    n: int,
    src: np.ndarray,
    dst: np.ndarray,
    wt: np.ndarray,
    labels: np.ndarray,
) -> Tuple[Dict[int, float], float]:
    """Compute per-cluster cohesion in [0, 1] and overall Newman modularity."""
    unique_cids = np.unique(labels)
    if n == 0 or src.size == 0:
        cohesion = {int(cid): 1.0 for cid in unique_cids}
        return cohesion, 0.0

    # Symmetrize undirected weights for modularity and boundary ratio
    undir_u = np.concatenate([src, dst])
    undir_v = np.concatenate([dst, src])
    undir_w = np.concatenate([wt, wt]) * 0.5
    m2 = float(undir_w.sum())
    if m2 <= 0.0:
        return {int(cid): 1.0 for cid in unique_cids}, 0.0

    deg = np.zeros(n, dtype=np.float64)
    np.add.at(deg, undir_u, undir_w)

    same_comm = labels[undir_u] == labels[undir_v]
    max_cid = int(unique_cids.max()) + 1
    internal_w = np.zeros(max_cid, dtype=np.float64)
    total_w = np.zeros(max_cid, dtype=np.float64)
    comm_deg = np.zeros(max_cid, dtype=np.float64)

    np.add.at(total_w, labels[undir_u], undir_w)
    if np.any(same_comm):
        np.add.at(internal_w, labels[undir_u[same_comm]], undir_w[same_comm])
    np.add.at(comm_deg, labels, deg)

    cohesion: Dict[int, float] = {}
    modularity = 0.0
    for cid_np in unique_cids:
        cid = int(cid_np)
        tot = float(total_w[cid])
        intl = float(internal_w[cid])
        if tot > 0.0:
            cohesion[cid] = round(min(1.0, max(0.0, intl / tot)), 6)
        else:
            cohesion[cid] = 1.0
        modularity += (intl / m2) - ((float(comm_deg[cid]) / m2) ** 2)

    return cohesion, round(float(modularity), 6)


def _split_oversized_clusters(
    n: int,
    src: np.ndarray,
    dst: np.ndarray,
    wt: np.ndarray,
    labels: np.ndarray,
    max_cluster_fraction: float,
) -> Tuple[np.ndarray, bool]:
    """Split clusters exceeding max(10, floor(n * max_cluster_fraction)) nodes."""
    if n < 10 or max_cluster_fraction <= 0.0 or max_cluster_fraction >= 1.0:
        return labels, False

    max_size = max(9, int(math.floor(n * max_cluster_fraction)))
    # Require at least 10 nodes to be considered oversized
    min_split_trigger = max(10, max_size + 1)

    current = labels.copy()
    split_applied = False

    for _round in range(3):
        unique_cids, counts = np.unique(current, return_counts=True)
        oversized = [
            int(cid)
            for cid, cnt in zip(unique_cids, counts)
            if int(cnt) >= min_split_trigger
        ]
        if not oversized:
            break

        split_applied = True
        next_cid = int(current.max()) + 1
        for cid in oversized:
            member_idx = np.where(current == cid)[0]
            sub_n = int(member_idx.size)
            local_map = np.full(n, -1, dtype=np.int64)
            local_map[member_idx] = np.arange(sub_n, dtype=np.int64)

            sub_labels: Optional[np.ndarray] = None
            mask = (local_map[src] >= 0) & (local_map[dst] >= 0)
            sub_src = local_map[src[mask]]
            sub_dst = local_map[dst[mask]]
            sub_wt = wt[mask]

            if sub_src.size > 0 and _module_available("networkx"):
                try:
                    import networkx as nx  # type: ignore

                    g = nx.Graph()
                    g.add_nodes_from(range(sub_n))
                    for u_i, v_i, w_i in zip(sub_src.tolist(), sub_dst.tolist(), sub_wt.tolist()):
                        if g.has_edge(u_i, v_i):
                            g[u_i][v_i]["weight"] += float(w_i)
                        else:
                            g.add_edge(u_i, v_i, weight=float(w_i))
                    comms = nx.community.louvain_communities(
                        g, weight="weight", resolution=1.75 + 0.5 * _round, seed=42
                    )
                    if len(comms) > 1:
                        sub_labels = np.zeros(sub_n, dtype=np.int64)
                        for idx_c, comm in enumerate(comms):
                            for node_local in comm:
                                sub_labels[int(node_local)] = idx_c
                except Exception:
                    sub_labels = None

            # Deterministic balanced partitioning if sub-graph is a dense clique or unsplit
            if sub_labels is None or len(np.unique(sub_labels)) <= 1:
                num_parts = max(2, int(math.ceil(sub_n / float(max_size))))
                part_size = int(math.ceil(sub_n / float(num_parts)))
                sub_labels = np.arange(sub_n, dtype=np.int64) // part_size

            for local_i, global_i in enumerate(member_idx.tolist()):
                sub_cid = int(sub_labels[local_i])
                if sub_cid == 0:
                    current[global_i] = cid
                else:
                    current[global_i] = next_cid + sub_cid - 1
            next_cid += int(sub_labels.max()) + 1

    return current, split_applied


class GraphGPUAccelerator:
    """Hardware-accelerated graph analytics and local/GPU batch embedding facade."""

    _st_lock = threading.Lock()
    _st_model_cache: Dict[Tuple[str, str], Any] = {}

    @classmethod
    def resolve_compute_backend(cls, device: str = "auto") -> Dict[str, Any]:
        """Resolve best available graph & tensor accelerator with CPU fallback metadata."""
        hw = _detect_compute_hardware()
        base_req, gpu_id = _parse_device_request(device)

        workers = max(
            1,
            int(getattr(env, "graphify_workers", min(8, os.cpu_count() or 4)) or 4),
        )
        fallback_to_cpu = bool(getattr(env, "graphify_fallback_to_cpu", True))

        cugraph_ok = bool(hw.get("cugraph_available", False)) and bool(
            hw.get("cuda_available", False)
        )
        torch_cuda_ok = bool(hw.get("torch_cuda", False))
        torch_mps_ok = bool(hw.get("torch_mps", False))

        backend = "numpy_blas_cpu"
        resolved_device = "cpu"
        use_gpu = False
        fallback_triggered = False

        if base_req == "cpu":
            backend = "numpy_blas_cpu"
            resolved_device = "cpu"
            use_gpu = False
        elif base_req == "cuda":
            if cugraph_ok:
                backend = "cugraph_cuda"
                resolved_device = f"cuda:{gpu_id}"
                use_gpu = True
            elif torch_cuda_ok:
                backend = "torch_cuda"
                resolved_device = f"cuda:{gpu_id}"
                use_gpu = True
            elif not fallback_to_cpu:
                backend = "torch_cuda"
                resolved_device = f"cuda:{gpu_id}"
                use_gpu = True
            else:
                backend = "numpy_blas_cpu"
                resolved_device = "cpu"
                use_gpu = False
                fallback_triggered = True
        elif base_req == "mps":
            if torch_mps_ok:
                backend = "torch_mps"
                resolved_device = "mps"
                use_gpu = True
            elif not fallback_to_cpu:
                backend = "torch_mps"
                resolved_device = "mps"
                use_gpu = True
            else:
                backend = "numpy_blas_cpu"
                resolved_device = "cpu"
                use_gpu = False
                fallback_triggered = True
        elif base_req == "gpu":
            if cugraph_ok:
                backend = "cugraph_cuda"
                resolved_device = f"cuda:{gpu_id}"
                use_gpu = True
            elif torch_cuda_ok:
                backend = "torch_cuda"
                resolved_device = f"cuda:{gpu_id}"
                use_gpu = True
            elif torch_mps_ok:
                backend = "torch_mps"
                resolved_device = "mps"
                use_gpu = True
            elif fallback_to_cpu:
                backend = "numpy_blas_cpu"
                resolved_device = "cpu"
                use_gpu = False
                fallback_triggered = True
            else:
                raise RuntimeError(
                    "Requested GPU compute backend, but neither CUDA nor Apple MPS is available and fallback_to_cpu=False."
                )
        else:  # "auto"
            if cugraph_ok:
                backend = "cugraph_cuda"
                resolved_device = f"cuda:{gpu_id}"
                use_gpu = True
            elif torch_cuda_ok:
                backend = "torch_cuda"
                resolved_device = f"cuda:{gpu_id}"
                use_gpu = True
            elif torch_mps_ok:
                backend = "torch_mps"
                resolved_device = "mps"
                use_gpu = True
            else:
                backend = "numpy_blas_cpu"
                resolved_device = "cpu"
                use_gpu = False

        return {
            "backend": backend,
            "device": resolved_device,
            "requested_device": base_req if base_req != "cuda" else f"cuda:{gpu_id}",
            "use_gpu": use_gpu,
            "gpu_id": gpu_id,
            "workers": workers,
            "fallback_to_cpu": fallback_to_cpu,
            "fallback_triggered": fallback_triggered,
            "cugraph_available": bool(hw.get("cugraph_available", False)),
            "torch_available": bool(hw.get("torch_available", False)),
            "torch_cuda": torch_cuda_ok,
            "torch_mps": torch_mps_ok,
            "igraph_available": bool(hw.get("igraph_available", False)),
            "scipy_available": bool(hw.get("scipy_available", False)),
            "networkx_available": bool(hw.get("networkx_available", False)),
            "graspologic_available": bool(hw.get("graspologic_available", False)),
        }

    @classmethod
    def cosine_similarity_topk(
        cls,
        matrix_a: Any,
        matrix_b: Any = None,
        top_k: int = 8,
        min_score: float = 0.60,
        device: str = "auto",
    ) -> Tuple[List[Tuple[int, int, float]], Dict[str, Any]]:
        """Compute L2-normalized top-k cosine similarity pairs with GPU->CPU fallback."""
        t0 = time.perf_counter()
        mat_a = _to_2d_float32(matrix_a)
        self_similarity = matrix_b is None
        mat_b = mat_a if self_similarity else _to_2d_float32(matrix_b)

        resolved = cls.resolve_compute_backend(device=device)
        backend_used = "numpy_blas_cpu"
        device_used = "cpu"
        fallback_triggered = False
        fallback_error: Optional[str] = None

        n_a = int(mat_a.shape[0])
        n_b = int(mat_b.shape[0])
        dim_a = int(mat_a.shape[1]) if mat_a.ndim == 2 else 0
        dim_b = int(mat_b.shape[1]) if mat_b.ndim == 2 else 0

        if dim_a != dim_b and n_a > 0 and n_b > 0:
            raise ValueError(
                f"Embedding dimension mismatch: matrix_a has {dim_a}, matrix_b has {dim_b}"
            )

        if n_a == 0 or n_b == 0 or dim_a == 0 or top_k <= 0 or (self_similarity and n_a < 2):
            meta = {
                "backend_used": resolved["backend"],
                "device_used": resolved["device"],
                "fallback_triggered": False,
                "fallback_error": None,
                "rows_a": n_a,
                "rows_b": n_b,
                "dim": dim_a,
                "top_k": int(top_k),
                "min_score": float(min_score),
                "pair_count": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            }
            return [], meta

        norm_a = _l2_normalize_rows(mat_a)
        norm_b = norm_a if self_similarity else _l2_normalize_rows(mat_b)

        k_eff = min(int(top_k), n_b - 1 if self_similarity else n_b)
        if k_eff <= 0:
            return [], {
                "backend_used": resolved["backend"],
                "device_used": resolved["device"],
                "fallback_triggered": False,
                "fallback_error": None,
                "rows_a": n_a,
                "rows_b": n_b,
                "dim": dim_a,
                "top_k": int(top_k),
                "min_score": float(min_score),
                "pair_count": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            }

        pairs: Optional[List[Tuple[int, int, float]]] = None
        use_torch_gpu = resolved["backend"] in ("cugraph_cuda", "torch_cuda", "torch_mps")

        if use_torch_gpu and resolved["torch_available"]:
            try:
                import torch  # type: ignore

                torch_dev = torch.device(resolved["device"])
                t_a = torch.from_numpy(norm_a).to(device=torch_dev, dtype=torch.float32)
                t_b = t_a if self_similarity else torch.from_numpy(norm_b).to(
                    device=torch_dev, dtype=torch.float32
                )

                chunk_rows = 4096
                chunk_cols = 4096
                gpu_pairs: List[Tuple[int, int, float]] = []

                with torch.no_grad():
                    for a_start in range(0, n_a, chunk_rows):
                        a_end = min(n_a, a_start + chunk_rows)
                        a_blk = t_a[a_start:a_end]

                        if n_b <= chunk_cols:
                            sims = torch.mm(a_blk, t_b.t())
                            if self_similarity:
                                row_ids = torch.arange(a_start, a_end, device=torch_dev)
                                sims[torch.arange(a_end - a_start, device=torch_dev), row_ids] = -float("inf")
                            vals, idxs = torch.topk(sims, k=k_eff, dim=1)
                            vals_cpu = vals.cpu().numpy()
                            idxs_cpu = idxs.cpu().numpy()
                            for local_r in range(a_end - a_start):
                                gi = a_start + local_r
                                for pos in range(k_eff):
                                    score = float(vals_cpu[local_r, pos])
                                    if score >= min_score and math.isfinite(score):
                                        gpu_pairs.append((gi, int(idxs_cpu[local_r, pos]), score))
                        else:
                            cand_vals_list = []
                            cand_idxs_list = []
                            for b_start in range(0, n_b, chunk_cols):
                                b_end = min(n_b, b_start + chunk_cols)
                                b_blk = t_b[b_start:b_end]
                                sims = torch.mm(a_blk, b_blk.t())
                                if self_similarity:
                                    ov_start = max(a_start, b_start)
                                    ov_end = min(a_end, b_end)
                                    if ov_start < ov_end:
                                        ov_r = torch.arange(
                                            ov_start - a_start, ov_end - a_start, device=torch_dev
                                        )
                                        ov_c = torch.arange(
                                            ov_start - b_start, ov_end - b_start, device=torch_dev
                                        )
                                        sims[ov_r, ov_c] = -float("inf")
                                local_k = min(k_eff, b_end - b_start)
                                v_blk, i_blk = torch.topk(sims, k=local_k, dim=1)
                                cand_vals_list.append(v_blk)
                                cand_idxs_list.append(i_blk + b_start)

                            merged_vals = torch.cat(cand_vals_list, dim=1)
                            merged_idxs = torch.cat(cand_idxs_list, dim=1)
                            final_vals, pos_idxs = torch.topk(merged_vals, k=k_eff, dim=1)
                            final_idxs = torch.gather(merged_idxs, 1, pos_idxs)
                            vals_cpu = final_vals.cpu().numpy()
                            idxs_cpu = final_idxs.cpu().numpy()
                            for local_r in range(a_end - a_start):
                                gi = a_start + local_r
                                for pos in range(k_eff):
                                    score = float(vals_cpu[local_r, pos])
                                    if score >= min_score and math.isfinite(score):
                                        gpu_pairs.append((gi, int(idxs_cpu[local_r, pos]), score))

                pairs = gpu_pairs
                backend_used = (
                    "torch_cuda"
                    if resolved["backend"] == "cugraph_cuda"
                    else resolved["backend"]
                )
                device_used = resolved["device"]
            except Exception as exc:
                _clear_torch_gpu_cache()
                if not resolved["fallback_to_cpu"]:
                    raise
                logger.warning(
                    "GPU cosine_similarity_topk failed on %s (%s); falling back to NumPy BLAS CPU",
                    resolved["device"],
                    exc,
                )
                fallback_triggered = True
                fallback_error = str(exc)
                pairs = None

        if pairs is None:
            cpu_pairs: List[Tuple[int, int, float]] = []
            chunk_rows = 2048
            for a_start in range(0, n_a, chunk_rows):
                a_end = min(n_a, a_start + chunk_rows)
                sims = norm_a[a_start:a_end] @ norm_b.T
                if self_similarity:
                    local_r = np.arange(a_end - a_start)
                    sims[local_r, a_start + local_r] = -np.inf

                if k_eff < n_b:
                    part_idx = np.argpartition(sims, -k_eff, axis=1)[:, -k_eff:]
                    row_range = np.arange(a_end - a_start)[:, None]
                    part_vals = sims[row_range, part_idx]
                    order = np.argsort(-part_vals, axis=1)
                    top_idxs = part_idx[row_range, order]
                    top_vals = part_vals[row_range, order]
                else:
                    order = np.argsort(-sims, axis=1)
                    row_range = np.arange(a_end - a_start)[:, None]
                    top_idxs = order[:, :k_eff]
                    top_vals = sims[row_range, top_idxs]

                for r_i in range(a_end - a_start):
                    gi = a_start + r_i
                    for pos in range(k_eff):
                        score = float(top_vals[r_i, pos])
                        if score >= min_score and math.isfinite(score):
                            cpu_pairs.append((gi, int(top_idxs[r_i, pos]), score))

            pairs = cpu_pairs
            backend_used = "numpy_blas_cpu"
            device_used = "cpu"

        pairs.sort(key=lambda item: (-item[2], item[0], item[1]))
        meta = {
            "backend_used": backend_used,
            "device_used": device_used,
            "fallback_triggered": fallback_triggered,
            "fallback_error": fallback_error,
            "rows_a": n_a,
            "rows_b": n_b,
            "dim": dim_a,
            "top_k": int(top_k),
            "min_score": float(min_score),
            "pair_count": len(pairs),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }
        return pairs, meta

    @classmethod
    def _tfidf_ngram_projection(
        cls,
        texts: Sequence[str],
        dim: int = _TFIDF_DIM,
        device: str = "auto",
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Fast (<5ms) deterministic token+char n-gram TF-IDF projection."""
        n = len(texts)
        if n == 0:
            return np.zeros((0, dim), dtype=np.float32), {
                "backend_used": "numpy_blas_cpu",
                "device_used": "cpu",
                "fallback_triggered": False,
            }

        mat = np.zeros((n, dim), dtype=np.float32)
        df = np.zeros(dim, dtype=np.float32)
        mask = dim - 1
        is_pow2 = (dim & mask) == 0

        for row_idx, raw_text in enumerate(texts):
            text = (raw_text or "").lower().strip()
            if not text:
                continue
            tokens = _TOKEN_RE.findall(text)
            row = mat[row_idx]
            seen_cols: set[int] = set()

            for tok in tokens:
                h = zlib.crc32(tok.encode("utf-8"))
                col = (h & mask) if is_pow2 else (h % dim)
                sign = 1.0 if ((h >> 8) & 1) == 0 else -1.0
                row[col] += sign
                seen_cols.add(col)

            # Token bigrams for phrase structure
            for i in range(len(tokens) - 1):
                bg = f"{tokens[i]}|{tokens[i + 1]}"
                h = zlib.crc32(bg.encode("utf-8"))
                col = (h & mask) if is_pow2 else (h % dim)
                sign = 1.0 if ((h >> 8) & 1) == 0 else -1.0
                row[col] += 0.75 * sign
                seen_cols.add(col)

            # Compact char 3-grams over bounded prefix for subword / camelCase similarity
            compact = text[:384]
            if len(compact) >= 3:
                for i in range(0, len(compact) - 2):
                    gram = compact[i : i + 3]
                    if gram == "   ":
                        continue
                    h = zlib.crc32(gram.encode("utf-8"))
                    col = (h & mask) if is_pow2 else (h % dim)
                    sign = 1.0 if ((h >> 8) & 1) == 0 else -1.0
                    row[col] += 0.35 * sign
                    seen_cols.add(col)

            if seen_cols:
                for col in seen_cols:
                    df[col] += 1.0

        # Sublinear TF * smooth IDF
        nonzero = mat != 0.0
        if np.any(nonzero):
            signed_log = np.sign(mat) * (1.0 + np.log1p(np.abs(mat)))
            mat = np.where(nonzero, signed_log, np.float32(0.0)).astype(np.float32)

        idf = (np.log((float(n) + 1.0) / (df + 1.0)) + 1.0).astype(np.float32)

        resolved = cls.resolve_compute_backend(device=device)
        backend_used = "numpy_blas_cpu"
        device_used = "cpu"
        fallback_triggered = False

        if (
            n >= 32
            and resolved["backend"] in ("cugraph_cuda", "torch_cuda", "torch_mps")
            and resolved["torch_available"]
        ):
            try:
                import torch  # type: ignore

                torch_dev = torch.device(resolved["device"])
                t_mat = torch.from_numpy(mat).to(device=torch_dev, dtype=torch.float32)
                t_idf = torch.from_numpy(idf).to(device=torch_dev, dtype=torch.float32)
                t_mat = t_mat * t_idf
                t_norms = torch.linalg.norm(t_mat, dim=1, keepdim=True)
                t_mat = torch.where(t_norms > 1e-12, t_mat / t_norms.clamp(min=1e-12), 0.0)
                out = t_mat.cpu().numpy().astype(np.float32, copy=False)
                backend_used = (
                    "torch_cuda"
                    if resolved["backend"] == "cugraph_cuda"
                    else resolved["backend"]
                )
                device_used = resolved["device"]
                return out, {
                    "backend_used": backend_used,
                    "device_used": device_used,
                    "fallback_triggered": False,
                }
            except Exception:
                _clear_torch_gpu_cache()
                if not resolved["fallback_to_cpu"]:
                    raise
                fallback_triggered = True

        mat *= idf
        out = _l2_normalize_rows(mat)
        return out, {
            "backend_used": backend_used,
            "device_used": device_used,
            "fallback_triggered": fallback_triggered,
        }

    @classmethod
    def _embed_sentence_transformers(
        cls,
        texts: List[str],
        device: str,
        batch_size: int,
        model_name: str,
        fallback_to_cpu: bool,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        from sentence_transformers import SentenceTransformer  # type: ignore

        resolved = cls.resolve_compute_backend(device=device)
        target_dev = "cpu"
        if resolved["backend"] in ("cugraph_cuda", "torch_cuda"):
            target_dev = resolved["device"]
        elif resolved["backend"] == "torch_mps":
            target_dev = "mps"

        def _encode_on(dev_str: str) -> np.ndarray:
            key = (model_name, dev_str)
            with cls._st_lock:
                st_model = cls._st_model_cache.get(key)
                if st_model is None:
                    st_model = SentenceTransformer(model_name, device=dev_str)
                    cls._st_model_cache[key] = st_model
            vecs = st_model.encode(
                texts,
                batch_size=max(1, int(batch_size)),
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return _l2_normalize_rows(np.asarray(vecs, dtype=np.float32))

        fallback_triggered = False
        fallback_error: Optional[str] = None
        try:
            out = _encode_on(target_dev)
            used_dev = target_dev
        except Exception as exc:
            if target_dev != "cpu":
                with cls._st_lock:
                    cls._st_model_cache.pop((model_name, target_dev), None)
                _clear_torch_gpu_cache()
            if target_dev != "cpu" and fallback_to_cpu:
                logger.warning(
                    "sentence_transformers failed on %s (%s); falling back to CPU",
                    target_dev,
                    exc,
                )
                fallback_triggered = True
                fallback_error = str(exc)
                out = _encode_on("cpu")
                used_dev = "cpu"
            else:
                raise

        return out, {
            "engine_used": "sentence_transformers",
            "model_used": model_name,
            "device_used": used_dev,
            "fallback_triggered": fallback_triggered,
            "fallback_error": fallback_error,
        }

    @classmethod
    def _embed_ollama_sync(
        cls,
        texts: List[str],
        batch_size: int,
        local_ai_url: Optional[str],
        model_name: str,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        base_url = (
            local_ai_url
            or getattr(env, "ollama_url", None)
            or "http://localhost:11434"
        ).rstrip("/")
        bsize = max(1, int(batch_size))
        all_vecs: List[List[float]] = []

        with httpx.Client(timeout=60.0) as client:
            for start in range(0, len(texts), bsize):
                chunk = texts[start : start + bsize]
                res = client.post(
                    f"{base_url}/api/embed",
                    json={"model": model_name, "input": chunk},
                )
                if res.status_code == 200:
                    data = res.json() or {}
                    embs = data.get("embeddings")
                    if isinstance(embs, list) and len(embs) == len(chunk):
                        all_vecs.extend(embs)
                        continue
                # Fallback to legacy /api/embeddings single-prompt endpoint
                for item in chunk:
                    r_single = client.post(
                        f"{base_url}/api/embeddings",
                        json={"model": model_name, "prompt": item},
                    )
                    if r_single.status_code != 200:
                        raise RuntimeError(
                            f"Ollama embeddings HTTP {r_single.status_code}: {r_single.text[:200]}"
                        )
                    single_data = r_single.json() or {}
                    vec = single_data.get("embedding")
                    if not isinstance(vec, list):
                        raise RuntimeError("Ollama embeddings response missing 'embedding'")
                    all_vecs.append(vec)

        arr = _l2_normalize_rows(np.asarray(all_vecs, dtype=np.float32))
        return arr, {
            "engine_used": "ollama",
            "model_used": model_name,
            "base_url": base_url,
            "device_used": "local_ai",
            "fallback_triggered": False,
            "fallback_error": None,
        }

    @classmethod
    def _embed_openai_compat_sync(
        cls,
        texts: List[str],
        engine_name: str,
        batch_size: int,
        local_ai_url: Optional[str],
        model_name: str,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        custom_cfg = _normalize_openai_base_url(
            getattr(env, "local_ai_base_url", "") or ""
        )
        base_url = _normalize_openai_base_url(local_ai_url or "")
        if not base_url:
            base_url = custom_cfg or _LOCAL_OPENAI_DEFAULT_URLS.get(
                engine_name, "http://localhost:8000/v1"
            )
        api_key = getattr(env, "local_ai_api_key", None) or "local-no-key"
        headers = {"Authorization": f"Bearer {api_key}"}
        bsize = max(1, int(batch_size))
        all_vecs: List[List[float]] = []

        with httpx.Client(timeout=60.0) as client:
            for start in range(0, len(texts), bsize):
                chunk = texts[start : start + bsize]
                res = client.post(
                    f"{base_url}/embeddings",
                    headers=headers,
                    json={"model": model_name, "input": chunk},
                )
                if res.status_code != 200:
                    raise RuntimeError(
                        f"{engine_name} embeddings HTTP {res.status_code}: {res.text[:200]}"
                    )
                payload = res.json() or {}
                items = payload.get("data") or []
                if len(items) != len(chunk):
                    raise RuntimeError(
                        f"{engine_name} embeddings returned {len(items)} vectors for {len(chunk)} inputs"
                    )
                items_sorted = sorted(
                    items, key=lambda x: int(x.get("index", 0)) if isinstance(x, dict) else 0
                )
                for item in items_sorted:
                    all_vecs.append(item["embedding"])

        arr = _l2_normalize_rows(np.asarray(all_vecs, dtype=np.float32))
        return arr, {
            "engine_used": engine_name,
            "model_used": model_name,
            "base_url": base_url,
            "device_used": "local_ai",
            "fallback_triggered": False,
            "fallback_error": None,
        }

    @classmethod
    def embed_texts(
        cls,
        texts: List[str],
        engine: str = "auto",
        device: str = "auto",
        batch_size: int = 64,
        local_ai_url: Optional[str] = None,
        model: Optional[str] = None,
        allow_fallback: bool = True,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Batch-embed texts via sentence_transformers, Ollama, vLLM/LMStudio/llama.cpp, or TF-IDF projection."""
        t0 = time.perf_counter()
        eng = (engine or "auto").strip().lower()
        fallback_to_cpu = bool(getattr(env, "graphify_fallback_to_cpu", True))
        default_st_model = getattr(env, "local_embed_model", "BAAI/bge-small-en-v1.5")

        if eng == "off":
            return np.zeros((len(texts), 0), dtype=np.float32), {
                "engine_used": "off",
                "disabled": True,
                "count": len(texts),
                "dim": 0,
                "fallback_triggered": False,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            }

        if not texts:
            return np.zeros((0, _TFIDF_DIM), dtype=np.float32), {
                "engine_used": eng,
                "count": 0,
                "dim": _TFIDF_DIM,
                "fallback_triggered": False,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            }

        fallback_triggered = False
        fallback_error: Optional[str] = None

        if eng in ("sentence_transformers", "gpu_embed"):
            st_model = model or default_st_model
            if _module_available("sentence_transformers"):
                try:
                    arr, meta = cls._embed_sentence_transformers(
                        texts, device, batch_size, st_model, fallback_to_cpu
                    )
                    meta.update(
                        {
                            "count": int(arr.shape[0]),
                            "dim": int(arr.shape[1]),
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
                        }
                    )
                    return arr, meta
                except Exception as exc:
                    if not allow_fallback:
                        raise
                    fallback_triggered = True
                    fallback_error = str(exc)
            elif not allow_fallback:
                raise RuntimeError("sentence_transformers is not installed")
            else:
                fallback_triggered = True
                fallback_error = "sentence_transformers not installed"

        elif eng == "ollama":
            ol_model = (
                model
                or getattr(env, "ollama_embedding_model", None)
                or "nomic-embed-text"
            )
            try:
                arr, meta = cls._embed_ollama_sync(
                    texts, batch_size, local_ai_url, ol_model
                )
                meta.update(
                    {
                        "count": int(arr.shape[0]),
                        "dim": int(arr.shape[1]),
                        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
                    }
                )
                return arr, meta
            except Exception as exc:
                if not allow_fallback:
                    raise
                fallback_triggered = True
                fallback_error = str(exc)

        elif eng in ("vllm", "lmstudio", "llamacpp"):
            oa_model = (
                model
                or getattr(env, "local_ai_model", "")
                or default_st_model
            )
            try:
                arr, meta = cls._embed_openai_compat_sync(
                    texts, eng, batch_size, local_ai_url, oa_model
                )
                meta.update(
                    {
                        "count": int(arr.shape[0]),
                        "dim": int(arr.shape[1]),
                        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
                    }
                )
                return arr, meta
            except Exception as exc:
                if not allow_fallback:
                    raise
                fallback_triggered = True
                fallback_error = str(exc)

        elif eng == "auto":
            if _module_available("sentence_transformers"):
                try:
                    arr, meta = cls._embed_sentence_transformers(
                        texts, device, batch_size, default_st_model, fallback_to_cpu
                    )
                    meta.update(
                        {
                            "count": int(arr.shape[0]),
                            "dim": int(arr.shape[1]),
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
                        }
                    )
                    return arr, meta
                except Exception as exc:
                    fallback_triggered = True
                    fallback_error = str(exc)
            elif local_ai_url:
                try:
                    if "11434" in local_ai_url:
                        ol_model = (
                            model
                            or getattr(env, "ollama_embedding_model", None)
                            or "nomic-embed-text"
                        )
                        arr, meta = cls._embed_ollama_sync(
                            texts, batch_size, local_ai_url, ol_model
                        )
                    else:
                        oa_model = (
                            model
                            or getattr(env, "local_ai_model", "")
                            or default_st_model
                        )
                        arr, meta = cls._embed_openai_compat_sync(
                            texts, "local_ai", batch_size, local_ai_url, oa_model
                        )
                    meta.update(
                        {
                            "count": int(arr.shape[0]),
                            "dim": int(arr.shape[1]),
                            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
                        }
                    )
                    return arr, meta
                except Exception as exc:
                    fallback_triggered = True
                    fallback_error = str(exc)

        # Deterministic vectorized TF-IDF n-gram projection fallback (<5ms, zero dependencies)
        arr, proj_meta = cls._tfidf_ngram_projection(
            texts, dim=_TFIDF_DIM, device=device
        )
        return arr, {
            "engine_used": "tfidf_ngram_projection",
            "backend_used": proj_meta["backend_used"],
            "device_used": proj_meta["device_used"],
            "model_used": "deterministic-ngram-tfidf-256",
            "count": int(arr.shape[0]),
            "dim": int(arr.shape[1]),
            "fallback_triggered": bool(
                fallback_triggered or proj_meta.get("fallback_triggered", False)
            ),
            "fallback_error": fallback_error,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }

    @classmethod
    def accelerated_pagerank_and_centrality(
        cls,
        node_ids: List[str],
        edges: List[Tuple[str, str, float]],
        device: str = "auto",
        damping: float = 0.85,
        max_iter: int = 30,
    ) -> Dict[str, Any]:
        """Compute PageRank and degree centralities via cuGraph -> PyTorch GPU -> NumPy."""
        t0 = time.perf_counter()
        n = len(node_ids)
        resolved = cls.resolve_compute_backend(device=device)
        if n == 0:
            return {
                "pagerank": {},
                "degree_centrality": {},
                "in_degree": {},
                "out_degree": {},
                "backend_used": resolved["backend"],
                "device_used": resolved["device"],
                "fallback_triggered": False,
                "fallback_error": None,
                "iterations": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            }

        _, src, dst, wt = _normalize_edges(node_ids, edges)
        in_deg = np.bincount(dst, minlength=n) if dst.size > 0 else np.zeros(n, dtype=np.int64)
        out_deg = np.bincount(src, minlength=n) if src.size > 0 else np.zeros(n, dtype=np.int64)
        denom = float(max(1, n - 1))
        deg_cent = (in_deg + out_deg).astype(np.float64) / denom if n > 1 else np.zeros(n, dtype=np.float64)

        if src.size == 0:
            uniform = 1.0 / float(n)
            return {
                "pagerank": {str(nid): uniform for nid in node_ids},
                "degree_centrality": {str(nid): float(deg_cent[i]) for i, nid in enumerate(node_ids)},
                "in_degree": {str(nid): int(in_deg[i]) for i, nid in enumerate(node_ids)},
                "out_degree": {str(nid): int(out_deg[i]) for i, nid in enumerate(node_ids)},
                "backend_used": resolved["backend"],
                "device_used": resolved["device"],
                "fallback_triggered": False,
                "fallback_error": None,
                "iterations": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            }

        out_weight_sum = np.zeros(n, dtype=np.float64)
        np.add.at(out_weight_sum, src, wt)
        norm_w = wt / np.maximum(out_weight_sum[src], 1e-12)
        dangling_mask = out_weight_sum <= 0.0

        pr_vec: Optional[np.ndarray] = None
        iters_run = 0
        backend_used = "numpy_blas_cpu"
        device_used = "cpu"
        fallback_triggered = False
        fallback_error: Optional[str] = None

        # Tier 1: cuGraph (CUDA)
        if resolved["backend"] == "cugraph_cuda":
            try:
                import cudf  # type: ignore
                import cugraph  # type: ignore

                df = cudf.DataFrame({"src": src, "dst": dst, "weight": wt})
                g_cu = cugraph.DiGraph()
                g_cu.from_cudf_edgelist(
                    df, source="src", destination="dst", edge_attr="weight", renumber=False
                )
                pr_df = cugraph.pagerank(g_cu, alpha=float(damping), max_iter=int(max_iter))
                pr_arr = np.full(n, 0.0, dtype=np.float64)
                verts = pr_df["vertex"].to_numpy()
                vals = pr_df["pagerank"].to_numpy()
                pr_arr[verts] = vals
                s_sum = float(pr_arr.sum())
                if s_sum > 0.0:
                    pr_arr /= s_sum
                pr_vec = pr_arr
                iters_run = int(max_iter)
                backend_used = "cugraph_cuda"
                device_used = resolved["device"]
            except Exception as exc:
                _clear_torch_gpu_cache()
                if not resolved["fallback_to_cpu"]:
                    raise
                logger.warning("cuGraph PageRank failed (%s); falling back", exc)
                fallback_triggered = True
                fallback_error = str(exc)

        # Tier 2: PyTorch CUDA sparse matrix-vector power iteration (MPS lacks sparse COO ops)
        if (
            pr_vec is None
            and (
                resolved["backend"] == "torch_cuda"
                or (resolved["backend"] == "cugraph_cuda" and resolved.get("torch_cuda"))
            )
            and resolved["torch_available"]
        ):
            try:
                import torch  # type: ignore

                torch_dev = torch.device(resolved["device"])
                indices = torch.tensor(
                    np.vstack([dst, src]), dtype=torch.long, device=torch_dev
                )
                values = torch.tensor(norm_w, dtype=torch.float32, device=torch_dev)
                trans_mat = torch.sparse_coo_tensor(
                    indices, values, size=(n, n)
                ).coalesce()

                r = torch.full((n,), 1.0 / float(n), dtype=torch.float32, device=torch_dev)
                dangling_t = torch.tensor(dangling_mask, dtype=torch.bool, device=torch_dev)
                base = (1.0 - float(damping)) / float(n)

                with torch.no_grad():
                    for step in range(1, max(1, int(max_iter)) + 1):
                        iters_run = step
                        dangling_sum = (
                            r[dangling_t].sum()
                            if torch.any(dangling_t)
                            else torch.tensor(0.0, dtype=torch.float32, device=torch_dev)
                        )
                        propagated = torch.sparse.mm(trans_mat, r.unsqueeze(1)).squeeze(1)
                        new_r = base + float(damping) * (propagated + dangling_sum / float(n))
                        diff = float(torch.abs(new_r - r).sum().item())
                        r = new_r
                        if diff < 1e-6:
                            break
                    r_sum = float(r.sum().item())
                    if r_sum > 0.0:
                        r = r / r_sum
                    pr_vec = r.cpu().numpy().astype(np.float64, copy=False)

                backend_used = (
                    "torch_cuda"
                    if resolved["backend"] == "cugraph_cuda"
                    else resolved["backend"]
                )
                device_used = resolved["device"]
            except Exception as exc:
                _clear_torch_gpu_cache()
                if not resolved["fallback_to_cpu"]:
                    raise
                logger.warning(
                    "PyTorch GPU PageRank failed on %s (%s); falling back to vectorized NumPy",
                    resolved["device"],
                    exc,
                )
                fallback_triggered = True
                fallback_error = str(exc)
                pr_vec = None

        # Tier 3: Vectorized SciPy CSR or NumPy array power iteration
        if pr_vec is None:
            r = np.full(n, 1.0 / float(n), dtype=np.float64)
            base = (1.0 - float(damping)) / float(n)
            csr = None
            if resolved["scipy_available"]:
                try:
                    import scipy.sparse as sp  # type: ignore

                    csr = sp.csr_matrix((norm_w, (dst, src)), shape=(n, n))
                except Exception:
                    csr = None

            for step in range(1, max(1, int(max_iter)) + 1):
                iters_run = step
                dangling_sum = float(r[dangling_mask].sum()) if np.any(dangling_mask) else 0.0
                if csr is not None:
                    new_r = base + float(damping) * (csr @ r + dangling_sum / float(n))
                else:
                    new_r = np.full(n, base + float(damping) * dangling_sum / float(n), dtype=np.float64)
                    np.add.at(new_r, dst, float(damping) * r[src] * norm_w)
                diff = float(np.abs(new_r - r).sum())
                r = new_r
                if diff < 1e-6:
                    break

            r_sum = float(r.sum())
            if r_sum > 0.0:
                r /= r_sum
            pr_vec = r
            backend_used = "scipy_csr_cpu" if csr is not None else "numpy_blas_cpu"
            device_used = "cpu"

        return {
            "pagerank": {str(nid): float(pr_vec[i]) for i, nid in enumerate(node_ids)},
            "degree_centrality": {str(nid): float(deg_cent[i]) for i, nid in enumerate(node_ids)},
            "in_degree": {str(nid): int(in_deg[i]) for i, nid in enumerate(node_ids)},
            "out_degree": {str(nid): int(out_deg[i]) for i, nid in enumerate(node_ids)},
            "backend_used": backend_used,
            "device_used": device_used,
            "fallback_triggered": fallback_triggered,
            "fallback_error": fallback_error,
            "iterations": iters_run,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }

    @classmethod
    def accelerated_clustering(
        cls,
        node_ids: List[str],
        edges: List[Tuple[str, str, float]],
        method: str = "leiden",
        device: str = "auto",
        max_cluster_fraction: float = 0.25,
    ) -> Dict[str, Any]:
        """Cluster graph nodes via cuGraph -> igraph -> graspologic -> PyTorch GPU -> NetworkX."""
        t0 = time.perf_counter()
        n = len(node_ids)
        resolved = cls.resolve_compute_backend(device=device)
        meth = (method or "leiden").strip().lower()

        if n == 0:
            return {
                "clusters": {},
                "communities": [],
                "cohesion": {},
                "cluster_count": 0,
                "modularity": 0.0,
                "split_applied": False,
                "backend_used": resolved["backend"],
                "device_used": resolved["device"],
                "fallback_triggered": False,
                "fallback_error": None,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            }

        _, src, dst, wt = _normalize_edges(node_ids, edges)
        labels: Optional[np.ndarray] = None
        backend_used = "networkx_louvain_cpu"
        device_used = "cpu"
        fallback_triggered = False
        fallback_error: Optional[str] = None

        if src.size == 0:
            labels = np.arange(n, dtype=np.int64)
            backend_used = resolved["backend"]
            device_used = resolved["device"]

        # Tier 1: cuGraph Leiden / Louvain (CUDA)
        if labels is None and resolved["backend"] == "cugraph_cuda":
            try:
                import cudf  # type: ignore
                import cugraph  # type: ignore

                undir_src = np.concatenate([src, dst])
                undir_dst = np.concatenate([dst, src])
                undir_wt = np.concatenate([wt, wt]) * 0.5
                df = cudf.DataFrame({"src": undir_src, "dst": undir_dst, "weight": undir_wt})
                g_cu = cugraph.Graph(directed=False)
                g_cu.from_cudf_edgelist(
                    df, source="src", destination="dst", edge_attr="weight", renumber=False
                )
                if meth == "louvain":
                    parts_df, _mod = cugraph.louvain(g_cu)
                else:
                    parts_df, _mod = cugraph.leiden(g_cu)
                lab = np.arange(n, dtype=np.int64) + int(n)
                verts = parts_df["vertex"].to_numpy()
                parts = parts_df["partition"].to_numpy()
                lab[verts] = parts
                labels = lab
                backend_used = "cugraph_cuda"
                device_used = resolved["device"]
            except Exception as exc:
                _clear_torch_gpu_cache()
                if not resolved["fallback_to_cpu"]:
                    raise
                logger.warning("cuGraph clustering failed (%s); falling back", exc)
                fallback_triggered = True
                fallback_error = str(exc)

        # Tier 2: igraph C-engine Leiden / Louvain
        if labels is None and resolved["igraph_available"] and meth in ("leiden", "louvain", "auto"):
            try:
                import igraph as ig  # type: ignore

                g_ig = ig.Graph(n=n, edges=list(zip(src.tolist(), dst.tolist())), directed=False)
                g_ig.es["weight"] = wt.tolist()
                g_ig.simplify(combine_edges={"weight": "sum"})
                if meth == "louvain":
                    part = g_ig.community_multilevel(weights=g_ig.es["weight"])
                else:
                    part = g_ig.community_leiden(
                        objective_function="modularity", weights=g_ig.es["weight"]
                    )
                labels = np.asarray(part.membership, dtype=np.int64)
                backend_used = "igraph_c_cpu"
                device_used = "cpu"
            except Exception as exc:
                logger.debug("igraph clustering skipped/failed: %s", exc)

        # Tier 3: graspologic Leiden
        if labels is None and resolved["graspologic_available"] and meth in ("leiden", "auto"):
            try:
                import networkx as nx  # type: ignore
                from graspologic.partition import leiden  # type: ignore

                g_nx = nx.Graph()
                g_nx.add_nodes_from(range(n))
                for u_i, v_i, w_i in zip(src.tolist(), dst.tolist(), wt.tolist()):
                    if g_nx.has_edge(u_i, v_i):
                        g_nx[u_i][v_i]["weight"] += float(w_i)
                    else:
                        g_nx.add_edge(u_i, v_i, weight=float(w_i))
                part_map = leiden(g_nx, random_seed=42)
                labels = np.asarray([int(part_map[i]) for i in range(n)], dtype=np.int64)
                backend_used = "graspologic_cpu"
                device_used = "cpu"
            except Exception as exc:
                logger.debug("graspologic clustering skipped/failed: %s", exc)

        # Tier 4: PyTorch CUDA parallel modularity label-propagation (MPS lacks sparse COO ops)
        if (
            labels is None
            and (
                resolved["backend"] == "torch_cuda"
                or (resolved["backend"] == "cugraph_cuda" and resolved.get("torch_cuda"))
            )
            and resolved["torch_available"]
            and n <= 4096
        ):
            try:
                import torch  # type: ignore

                torch_dev = torch.device(resolved["device"])
                undir_u = np.concatenate([src, dst])
                undir_v = np.concatenate([dst, src])
                undir_w = np.concatenate([wt, wt]) * 0.5

                indices = torch.tensor(
                    np.vstack([undir_u, undir_v]), dtype=torch.long, device=torch_dev
                )
                values = torch.tensor(undir_w, dtype=torch.float32, device=torch_dev)
                adj = torch.sparse_coo_tensor(indices, values, size=(n, n)).coalesce()

                deg_np = np.zeros(n, dtype=np.float32)
                np.add.at(deg_np, undir_u, undir_w.astype(np.float32))
                k = torch.from_numpy(deg_np).to(device=torch_dev)
                m2 = float(k.sum().item())

                if m2 > 0.0:
                    curr = torch.arange(n, dtype=torch.long, device=torch_dev)
                    eye_idx = torch.arange(n, dtype=torch.long, device=torch_dev)
                    with torch.no_grad():
                        for step in range(15):
                            uniq, inverse = torch.unique(curr, return_inverse=True)
                            k_cnt = int(uniq.numel())
                            l_mat = torch.zeros((n, k_cnt), dtype=torch.float32, device=torch_dev)
                            l_mat[eye_idx, inverse] = 1.0
                            comm_w = torch.sparse.mm(adj, l_mat)
                            tot_k = torch.matmul(k.unsqueeze(0), l_mat).squeeze(0)
                            gain = comm_w - (k.unsqueeze(1) * tot_k.unsqueeze(0)) / m2
                            best_cols = torch.argmax(gain, dim=1)
                            proposed = uniq[best_cols]
                            # Parity update prevents synchronous oscillation
                            upd_mask = (eye_idx % 2) == (step % 2)
                            next_labels = torch.where(upd_mask, proposed, curr)
                            if torch.equal(next_labels, curr) and step >= 1:
                                break
                            curr = next_labels
                    labels = curr.cpu().numpy().astype(np.int64, copy=False)
                else:
                    labels = np.arange(n, dtype=np.int64)

                backend_used = (
                    "torch_cuda"
                    if resolved["backend"] == "cugraph_cuda"
                    else resolved["backend"]
                )
                device_used = resolved["device"]
            except Exception as exc:
                _clear_torch_gpu_cache()
                if not resolved["fallback_to_cpu"]:
                    raise
                logger.warning(
                    "PyTorch GPU clustering failed on %s (%s); falling back to CPU Louvain",
                    resolved["device"],
                    exc,
                )
                fallback_triggered = True
                fallback_error = str(exc)
                labels = None

        # Tier 5: NetworkX Louvain / NumPy label-propagation CPU fallback
        if labels is None:
            if resolved["networkx_available"]:
                import networkx as nx  # type: ignore

                g_nx = nx.Graph()
                g_nx.add_nodes_from(range(n))
                for u_i, v_i, w_i in zip(src.tolist(), dst.tolist(), wt.tolist()):
                    if g_nx.has_edge(u_i, v_i):
                        g_nx[u_i][v_i]["weight"] += float(w_i)
                    else:
                        g_nx.add_edge(u_i, v_i, weight=float(w_i))
                comms = nx.community.louvain_communities(g_nx, weight="weight", seed=42)
                lab = np.zeros(n, dtype=np.int64)
                for c_idx, comm in enumerate(comms):
                    for node_i in comm:
                        lab[int(node_i)] = c_idx
                labels = lab
                backend_used = "networkx_louvain_cpu"
                device_used = "cpu"
            else:
                # Zero-dependency deterministic union-find connected components fallback
                parent = list(range(n))

                def _find(x: int) -> int:
                    while parent[x] != x:
                        parent[x] = parent[parent[x]]
                        x = parent[x]
                    return x

                for u_i, v_i in zip(src.tolist(), dst.tolist()):
                    ru, rv = _find(u_i), _find(v_i)
                    if ru != rv:
                        if ru < rv:
                            parent[rv] = ru
                        else:
                            parent[ru] = rv
                labels = np.asarray([_find(i) for i in range(n)], dtype=np.int64)
                backend_used = "numpy_components_cpu"
                device_used = "cpu"

        # Oversized cluster splitting (>25%, min 10 nodes)
        labels, split_applied = _split_oversized_clusters(
            n, src, dst, wt, labels, max_cluster_fraction
        )

        # Canonicalize cluster IDs 0..K-1 sorted by descending size, then lowest member index
        raw_groups: Dict[int, List[int]] = {}
        for idx_node, cid_np in enumerate(labels):
            raw_groups.setdefault(int(cid_np), []).append(idx_node)

        sorted_groups = sorted(
            raw_groups.values(),
            key=lambda members: (-len(members), members[0]),
        )
        canonical_labels = np.zeros(n, dtype=np.int64)
        for new_cid, members in enumerate(sorted_groups):
            for m_idx in members:
                canonical_labels[m_idx] = new_cid

        cohesion, modularity = _compute_cohesion_and_modularity(
            n, src, dst, wt, canonical_labels
        )

        communities: List[Dict[str, Any]] = []
        for new_cid, members in enumerate(sorted_groups):
            communities.append(
                {
                    "cluster_id": new_cid,
                    "nodes": [str(node_ids[m_idx]) for m_idx in members],
                    "size": len(members),
                    "cohesion": cohesion.get(new_cid, 1.0),
                }
            )

        clusters_map = {
            str(node_ids[i]): int(canonical_labels[i]) for i in range(n)
        }

        return {
            "clusters": clusters_map,
            "communities": communities,
            "cohesion": cohesion,
            "cluster_count": len(communities),
            "modularity": modularity,
            "split_applied": split_applied,
            "backend_used": backend_used,
            "device_used": device_used,
            "fallback_triggered": fallback_triggered,
            "fallback_error": fallback_error,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        """Report full Graphify GPU/MPS/CUDA/cuGraph/PyTorch/NumPy/Local-AI readiness."""
        hw = _detect_compute_hardware()
        resolved = cls.resolve_compute_backend(
            device=getattr(env, "graphify_device", "auto")
        )
        ollama_url = (
            getattr(env, "ollama_url", None) or "http://localhost:11434"
        ).rstrip("/")
        local_ai_base = _normalize_openai_base_url(
            getattr(env, "local_ai_base_url", "") or ""
        )

        return {
            "ready": True,
            "resolved_backend": resolved,
            "hardware": {
                "os": hw.get("os"),
                "arch": hw.get("arch"),
                "cpu_count": hw.get("cpu_count"),
                "apple_silicon": bool(hw.get("apple_silicon", False)),
                "cuda_available": bool(hw.get("cuda_available", False)),
                "cuda_device_count": int(hw.get("cuda_device_count", 0) or 0),
                "mps_available": bool(hw.get("mps_available", False)),
                "gpu_names": list(hw.get("gpu_names") or []),
            },
            "libraries": {
                "numpy": True,
                "numpy_version": hw.get("numpy_version"),
                "torch": bool(hw.get("torch_available", False)),
                "torch_cuda": bool(hw.get("torch_cuda", False)),
                "torch_mps": bool(hw.get("torch_mps", False)),
                "cugraph": bool(hw.get("cugraph_available", False)),
                "sentence_transformers": bool(
                    hw.get("sentence_transformers_available", False)
                ),
                "onnxruntime": bool(hw.get("onnxruntime_available", False)),
                "onnx_providers": list(hw.get("onnx_providers") or []),
                "igraph": bool(hw.get("igraph_available", False)),
                "scipy": bool(hw.get("scipy_available", False)),
                "networkx": bool(hw.get("networkx_available", False)),
                "graspologic": bool(hw.get("graspologic_available", False)),
            },
            "config": {
                "graphify_device": getattr(env, "graphify_device", "auto"),
                "graphify_workers": getattr(env, "graphify_workers", 4),
                "graphify_semantic_engine": getattr(
                    env, "graphify_semantic_engine", "auto"
                ),
                "graphify_fallback_to_cpu": getattr(
                    env, "graphify_fallback_to_cpu", True
                ),
                "embed_device": getattr(env, "embed_device", "auto"),
                "local_embed_model": getattr(
                    env, "local_embed_model", "BAAI/bge-small-en-v1.5"
                ),
            },
            "local_ai": {
                "ollama_url": ollama_url,
                "local_ai_base_url": local_ai_base or None,
                "default_endpoints": dict(_LOCAL_OPENAI_DEFAULT_URLS),
            },
        }
