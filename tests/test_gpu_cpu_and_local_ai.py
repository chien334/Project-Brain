"""Comprehensive deterministic unit & integration tests for GPU/CPU compute management,
Local AI Vision/OCR runtimes, and GraphGPUAccelerator / Graphify / CodeDocLinker acceleration.

All external network calls and GPU libraries are cleanly mocked via httpx.MockTransport
and sys.modules / monkeypatch so the full suite runs deterministically in <5s on CPU/Mac CI.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sys
import types
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import networkx as nx
import numpy as np
import pytest
from PIL import Image

from projectbrain.ai.ollama import OllamaAdapter
from projectbrain.core.config import env
from projectbrain.graphify_engine.extractor import GraphifyExtractor
from projectbrain.graphify_engine.gpu_accelerator import GraphGPUAccelerator
from projectbrain.graphify_engine.kb_linker import CodeDocLinker, DocChunk
from projectbrain.graphify_engine.models import GraphDelta, GraphEdge, GraphNode
from projectbrain.memory.embed import emb_dispatch
from projectbrain.ocr_runtime import (
    ComputeDeviceManager,
    LocalVisionParser,
    UnifiedOCREngine,
)
from projectbrain.plugins import PluginCategory, PluginRegistry
from projectbrain.utils.doc_parser import docx_to_markdown


# ---------------------------------------------------------------------------
# Local Test Helpers
# ---------------------------------------------------------------------------
def _make_png_bytes(width: int = 8, height: int = 8, color: Tuple[int, int, int] = (20, 120, 220)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _make_docx_with_image(text: str, image_name: str, image_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )
        zf.writestr(f"word/media/{image_name}", image_bytes)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. ComputeDeviceManager Tests
# ---------------------------------------------------------------------------
class TestComputeDeviceManager:
    def test_detect_hardware_structure(self) -> None:
        hw = ComputeDeviceManager.detect_hardware()
        expected_keys = {
            "os",
            "arch",
            "is_macos",
            "is_arm64",
            "is_x86_64",
            "cpu_count",
            "mkldnn_eligible",
            "cuda_available",
            "cuda_device_count",
            "paddle_cuda",
            "torch_cuda",
            "gpu_names",
            "nvidia_smi_gpus",
            "apple_silicon",
            "mps_available",
            "torch_mps",
            "metal_available",
            "onnxruntime_available",
            "onnx_providers",
            "tesseract_available",
            "tesseract_path",
            "pytesseract_available",
            "paddleocr_available",
            "rapidocr_available",
        }
        assert expected_keys.issubset(set(hw.keys()))
        assert isinstance(hw["cpu_count"], int) and hw["cpu_count"] >= 1
        assert isinstance(hw["arch"], str) and len(hw["arch"]) > 0
        assert isinstance(hw["cuda_available"], bool)
        assert isinstance(hw["mps_available"], bool)
        assert isinstance(hw["paddle_cuda"], bool)
        assert isinstance(hw["onnx_providers"], list)

    def test_resolve_device_cpu_and_gpu_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        no_gpu_hw = {
            "cpu_count": 8,
            "mkldnn_eligible": True,
            "cuda_available": False,
            "paddle_cuda": False,
            "mps_available": False,
            "apple_silicon": False,
            "onnx_providers": ["CPUExecutionProvider"],
        }

        cpu_res = ComputeDeviceManager.resolve_device("cpu", engine="paddle", hardware=no_gpu_hw)
        assert cpu_res["use_gpu"] is False
        assert cpu_res["device_str"] == "cpu"
        assert cpu_res["onnx_providers"] == ["CPUExecutionProvider"]

        # fallback_to_cpu=True -> gracefully resolves to CPU when GPU unavailable
        monkeypatch.setattr(env, "ocr_fallback_to_cpu", True)
        fb_res = ComputeDeviceManager.resolve_device("gpu", engine="paddle", hardware=no_gpu_hw)
        assert fb_res["use_gpu"] is False
        assert fb_res["device_str"] == "cpu"
        assert fb_res["fallback_to_cpu"] is True

        # fallback_to_cpu=False -> keeps GPU request even when hardware probe shows unavailable
        monkeypatch.setattr(env, "ocr_fallback_to_cpu", False)
        strict_paddle = ComputeDeviceManager.resolve_device("gpu", engine="paddle", hardware=no_gpu_hw)
        assert strict_paddle["use_gpu"] is True
        assert strict_paddle["device_str"] == "gpu:0"
        assert strict_paddle["fallback_to_cpu"] is False

        strict_onnx = ComputeDeviceManager.resolve_device("gpu", engine="rapidocr", hardware=no_gpu_hw)
        assert strict_onnx["use_gpu"] is True
        assert strict_onnx["device_str"] == "cuda:0"
        assert strict_onnx["onnx_providers"] == ["CPUExecutionProvider"]

        no_onnx_probe_hw = {**no_gpu_hw, "onnx_providers": []}
        strict_onnx_default = ComputeDeviceManager.resolve_device(
            "gpu", engine="rapidocr", hardware=no_onnx_probe_hw
        )
        assert strict_onnx_default["use_gpu"] is True
        assert "CUDAExecutionProvider" in strict_onnx_default["onnx_providers"]

    def test_smart_enable_mkldnn_by_architecture(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(env, "ocr_enable_mkldnn", "auto")

        # ARM64 / macOS -> mkldnn disabled
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        hw_mac_arm = ComputeDeviceManager.detect_hardware()
        assert hw_mac_arm["is_arm64"] is True
        assert hw_mac_arm["mkldnn_eligible"] is False
        res_mac = ComputeDeviceManager.resolve_device("cpu", engine="paddle", hardware=hw_mac_arm)
        assert res_mac["enable_mkldnn"] is False

        # Linux aarch64 -> mkldnn disabled
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("platform.machine", lambda: "aarch64")
        hw_linux_arm = ComputeDeviceManager.detect_hardware()
        assert hw_linux_arm["is_arm64"] is True
        assert hw_linux_arm["mkldnn_eligible"] is False
        res_linux_arm = ComputeDeviceManager.resolve_device("cpu", engine="paddle", hardware=hw_linux_arm)
        assert res_linux_arm["enable_mkldnn"] is False

        # Linux x86_64 -> mkldnn enabled
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("platform.machine", lambda: "x86_64")
        hw_x86 = ComputeDeviceManager.detect_hardware()
        assert hw_x86["is_x86_64"] is True
        assert hw_x86["mkldnn_eligible"] is True
        res_x86 = ComputeDeviceManager.resolve_device("cpu", engine="paddle", hardware=hw_x86)
        assert res_x86["enable_mkldnn"] is True

    @pytest.mark.asyncio
    async def test_probe_local_ai_endpoints_handles_unreachable_and_parses_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env, "ollama_url", "http://localhost:11434")
        monkeypatch.setattr(env, "local_ai_base_url", "")
        monkeypatch.setattr(env, "local_ai_api_key", "")

        seen_auth: Dict[str, Optional[str]] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            port = request.url.port
            path = request.url.path
            if port == 11434 and path == "/api/tags":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {"name": "qwen2.5vl:7b"},
                            {"name": "nomic-embed-text"},
                        ]
                    },
                )
            if port == 8000 and path == "/v1/models":
                seen_auth["vllm"] = request.headers.get("authorization")
                return httpx.Response(
                    200,
                    json={"data": [{"id": "Qwen/Qwen2.5-VL-7B-Instruct"}]},
                )
            if port == 1234:
                raise httpx.ConnectError("Connection refused (LM Studio offline)", request=request)
            return httpx.Response(503, text="Service unavailable")

        transport = httpx.MockTransport(handler)
        result = await ComputeDeviceManager.probe_local_ai_endpoints(
            timeout=1.0, transport=transport
        )

        assert result["ollama"]["reachable"] is True
        assert result["ollama"]["models"] == ["qwen2.5vl:7b", "nomic-embed-text"]

        assert result["vllm"]["reachable"] is True
        assert result["vllm"]["models"] == ["Qwen/Qwen2.5-VL-7B-Instruct"]
        assert seen_auth["vllm"] == "Bearer local-no-key"

        assert result["lmstudio"]["reachable"] is False
        assert result["lmstudio"]["models"] == []
        assert "Connection refused" in (result["lmstudio"]["error"] or "")

        assert result["llamacpp"]["reachable"] is False
        assert result["llamacpp"]["error"] == "HTTP 503"


# ---------------------------------------------------------------------------
# 2. UnifiedOCREngine, LocalVisionParser, OllamaAdapter, DocParser & Plugin
# ---------------------------------------------------------------------------
class TestOCRAndLocalVision:
    @pytest.mark.asyncio
    async def test_paddle_gpu_oom_falls_back_to_cpu_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        png = _make_png_bytes()
        monkeypatch.setattr(env, "ocr_fallback_to_cpu", True)
        monkeypatch.setattr(env, "ocr_paddle_lang", "en")

        def fake_resolve(requested: Optional[str] = "auto", engine: str = "paddle", hardware=None):
            if requested == "cpu":
                return {
                    "use_gpu": False,
                    "device_str": "cpu",
                    "gpu_id": 0,
                    "gpu_mem_mb": 2000,
                    "use_tensorrt": False,
                    "precision": "fp32",
                    "cpu_threads": 4,
                    "enable_mkldnn": False,
                    "onnx_providers": ["CPUExecutionProvider"],
                    "fallback_to_cpu": True,
                }
            return {
                "use_gpu": True,
                "device_str": "gpu:0",
                "gpu_id": 0,
                "gpu_mem_mb": 2000,
                "use_tensorrt": False,
                "precision": "fp32",
                "cpu_threads": 4,
                "enable_mkldnn": False,
                "onnx_providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                "fallback_to_cpu": True,
            }

        monkeypatch.setattr(ComputeDeviceManager, "resolve_device", classmethod(lambda cls, requested="auto", engine="paddle", hardware=None: fake_resolve(requested, engine, hardware)))

        class _FakeGPUFailPaddle:
            def predict(self, img_arr):
                raise RuntimeError("CUDA out of memory")

        class _FakeCPUPaddle:
            def predict(self, img_arr):
                return [
                    {
                        "rec_texts": ["UART_BOOT_OK", "FW_V3"],
                        "rec_scores": [0.97, 0.95],
                        "rec_polys": [
                            [[0, 0], [50, 0], [50, 12], [0, 12]],
                            [[60, 0], [110, 0], [110, 12], [60, 12]],
                        ],
                    }
                ]

        built_devices: List[str] = []

        def fake_build(lang: str, resolved: Dict[str, Any]):
            built_devices.append(resolved["device_str"])
            if resolved["use_gpu"]:
                return _FakeGPUFailPaddle()
            return _FakeCPUPaddle()

        monkeypatch.setattr(UnifiedOCREngine, "_build_paddle_engine", staticmethod(fake_build))

        engine = UnifiedOCREngine()
        res = await engine.extract_image(png, engine="paddle", device="gpu", lang="en")

        assert built_devices == ["gpu:0", "cpu"]
        assert res["engine_used"] == "paddle"
        assert res["device_used"] == "cpu"
        assert res["fallback_triggered"] is True
        assert "UART_BOOT_OK" in res["text"]
        assert "FW_V3" in res["text"]
        assert res["confidence"] > 0.9

        # When fallback_to_cpu=False, the OOM error propagates instead of falling back
        monkeypatch.setattr(env, "ocr_fallback_to_cpu", False)
        monkeypatch.setattr(
            ComputeDeviceManager,
            "resolve_device",
            classmethod(
                lambda cls, requested="auto", engine="paddle", hardware=None: {
                    **fake_resolve(requested, engine, hardware),
                    "fallback_to_cpu": False,
                }
            ),
        )
        engine_strict = UnifiedOCREngine()
        with pytest.raises(RuntimeError, match="CUDA out of memory"):
            await engine_strict.extract_image(png, engine="paddle", device="gpu", lang="en")

    @pytest.mark.asyncio
    async def test_ollama_adapter_vision_chat_formats_base64_images(self) -> None:
        png = _make_png_bytes()
        expected_b64 = base64.b64encode(png).decode("ascii")
        captured: List[Dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            captured.append({"url": str(request.url), "payload": payload})
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": "# Extracted Heading\nOK"}},
            )

        adapter = OllamaAdapter(
            base_url="http://localhost:11434",
            transport=httpx.MockTransport(handler),
        )
        out = await adapter.vision_chat("Transcribe image", [png], model="qwen2.5vl:7b")

        assert out == "# Extracted Heading\nOK"
        assert len(captured) == 1
        assert captured[0]["url"] == "http://localhost:11434/api/chat"
        body = captured[0]["payload"]
        assert body["model"] == "qwen2.5vl:7b"
        assert body["stream"] is False
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][0]["content"] == "Transcribe image"
        assert body["messages"][0]["images"] == [expected_b64]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("provider", "expected_url"),
        [
            ("vllm", "http://localhost:8000/v1/chat/completions"),
            ("lmstudio", "http://localhost:1234/v1/chat/completions"),
            ("llamacpp", "http://localhost:8081/v1/chat/completions"),
        ],
    )
    async def test_keyless_local_vision_parser_openai_compatible(
        self,
        provider: str,
        expected_url: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(env, "local_ai_base_url", "")
        monkeypatch.setattr(env, "local_ai_api_key", "")
        png = _make_png_bytes()
        expected_b64 = base64.b64encode(png).decode("ascii")
        captured: List[Dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(
                {
                    "url": str(request.url),
                    "auth": request.headers.get("authorization"),
                    "body": json.loads(request.content.decode("utf-8")),
                }
            )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "| Pin | Voltage |\n| --- | --- |\n| VCC | 3.3V |"}}]},
            )

        parser = LocalVisionParser(
            provider=provider,
            model="qwen2.5-vl-local",
            transport=httpx.MockTransport(handler),
        )
        md = await parser.parse_image(png, mime_type="image/png")

        assert "| Pin | Voltage |" in md
        assert len(captured) == 1
        assert captured[0]["url"] == expected_url
        assert captured[0]["auth"] == "Bearer local-no-key"
        content_parts = captured[0]["body"]["messages"][0]["content"]
        assert content_parts[0]["type"] == "text"
        assert content_parts[1]["type"] == "image_url"
        assert content_parts[1]["image_url"]["url"] == f"data:image/png;base64,{expected_b64}"

    @pytest.mark.asyncio
    async def test_hybrid_local_combines_fast_ocr_and_local_llm_refinement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        png = _make_png_bytes()
        captured_prompts: List[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            captured_prompts.append(body["messages"][0]["content"])
            return httpx.Response(
                200,
                json={
                    "message": {
                        "role": "assistant",
                        "content": "| Reg | Value |\n| --- | --- |\n| CFSR | 0x8200 |",
                    }
                },
            )

        engine = UnifiedOCREngine(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(
            engine,
            "_extract_paddle_blocking",
            lambda img, lang, dev: {
                "text": "Reg | Value\nCFSR 0x8200",
                "engine_used": "paddle",
                "device_used": "cpu",
                "confidence": 0.78,
                "fallback_triggered": False,
            },
        )

        res = await engine.extract_image(
            png,
            engine="hybrid_local",
            device="cpu",
            local_ai_provider="ollama",
            local_ai_model="qwen2.5vl:7b",
            local_ai_url="http://localhost:11434",
        )

        assert res["engine_used"] == "hybrid_local:paddle+ollama"
        assert res["device_used"] == "cpu"
        assert res["confidence"] >= 0.9
        assert "| CFSR | 0x8200 |" in res["text"]
        assert len(captured_prompts) == 1
        assert "CFSR 0x8200" in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_extract_batch_preserves_exact_order_under_out_of_order_delays(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = UnifiedOCREngine()
        images = [f"img-{i}".encode("utf-8") for i in range(6)]
        delays = [0.05, 0.01, 0.04, 0.005, 0.03, 0.001]

        async def fake_extract_image(image_bytes: bytes, **kwargs: Any) -> Dict[str, Any]:
            idx = int(image_bytes.decode("utf-8").split("-")[1])
            await asyncio.sleep(delays[idx])
            return {
                "text": f"text-{idx}",
                "engine_used": "mock",
                "device_used": "cpu",
                "confidence": 0.99,
                "fallback_triggered": False,
            }

        monkeypatch.setattr(engine, "extract_image", fake_extract_image)
        results = await engine.extract_batch(images, max_concurrency=3, engine="paddle")

        assert [r["text"] for r in results] == [f"text-{i}" for i in range(6)]

    @pytest.mark.asyncio
    async def test_docx_embedded_media_image_ocr_integration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        png = _make_png_bytes()
        docx_bytes = _make_docx_with_image("Firmware Architecture Overview", "image1.png", png)

        seen_calls: List[Tuple[bytes, Optional[str], Optional[str]]] = []

        async def fake_extract_image(self, image_bytes: bytes, mime_type: str = "image/png", engine=None, device=None, **kwargs):
            seen_calls.append((image_bytes, engine, device))
            return {
                "text": "AT+BLE_PAIR -> PAIRED_EVENT",
                "engine_used": "paddle",
                "device_used": "cpu",
                "confidence": 0.95,
                "fallback_triggered": False,
            }

        monkeypatch.setattr(UnifiedOCREngine, "extract_image", fake_extract_image)
        md = await docx_to_markdown(docx_bytes, ocr_options={"engine": "paddle", "device": "cpu"})

        assert "Firmware Architecture Overview" in md
        assert "### Embedded Media: image1.png" in md
        assert "AT+BLE_PAIR -> PAIRED_EVENT" in md
        assert len(seen_calls) == 1
        assert seen_calls[0][0] == png
        assert seen_calls[0][1] == "paddle"
        assert seen_calls[0][2] == "cpu"

    @pytest.mark.asyncio
    async def test_local_ai_ocr_parser_plugin_registration_and_execution(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        reg = PluginRegistry()
        reg.discover_builtins()
        plugin = reg.resolve(PluginCategory.ETL_PARSER, preferred_id="etl.local_ai_ocr")
        assert plugin.metadata.plugin_id == "etl.local_ai_ocr"
        assert plugin.can_parse("schematic.png") is True
        assert plugin.can_parse("datasheet.pdf", format_hint="local_ai_ocr") is True

        async def fake_extract_image(self, image_bytes: bytes, **kwargs: Any) -> Dict[str, Any]:
            return {
                "text": "# Pinout Spec\n\n`UART1_TX` connects to `PA9`.",
                "engine_used": "ollama",
                "device_used": "mps",
                "confidence": 0.93,
                "fallback_triggered": False,
            }

        monkeypatch.setattr(UnifiedOCREngine, "extract_image", fake_extract_image)

        img_path = tmp_path / "schematic.png"
        img_path.write_bytes(_make_png_bytes())

        out = await plugin.parse(
            str(img_path),
            source_name="schematic.png",
            project_id="proj-ocr",
            branch="main",
            version_tag="hw-v2.0",
            metadata={"ocr_engine": "ollama", "ocr_device": "auto", "local_ai_model": "qwen2.5vl:7b"},
        )

        assert out["plugin_id"] == "etl.local_ai_ocr"
        assert out["document"]["ocr_engine"] == "ollama"
        assert out["document"]["ocr_device"] == "mps"
        assert "Pinout Spec" in out["document"]["markdown_content"]
        assert len(out["chunks"]) >= 1
        assert len(out["graph_delta"]["nodes"]) >= 1


# ---------------------------------------------------------------------------
# 3. GraphGPUAccelerator, embed.py, GraphifyExtractor & CodeDocLinker
# ---------------------------------------------------------------------------
class TestGraphAcceleratorAndSemanticLinking:
    def test_resolve_compute_backend_modes_and_fallbacks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import projectbrain.graphify_engine.gpu_accelerator as ga_mod

        cpu_hw = {
            "torch_available": False,
            "torch_cuda": False,
            "torch_mps": False,
            "cuda_available": False,
            "cugraph_available": False,
            "igraph_available": False,
            "scipy_available": False,
            "networkx_available": True,
            "graspologic_available": False,
        }
        monkeypatch.setattr(ga_mod, "_detect_compute_hardware", lambda: dict(cpu_hw))
        monkeypatch.setattr(env, "graphify_fallback_to_cpu", True)

        assert GraphGPUAccelerator.resolve_compute_backend("cpu")["backend"] == "numpy_blas_cpu"
        assert GraphGPUAccelerator.resolve_compute_backend("auto")["backend"] == "numpy_blas_cpu"

        fb_cuda = GraphGPUAccelerator.resolve_compute_backend("cuda")
        assert fb_cuda["backend"] == "numpy_blas_cpu"
        assert fb_cuda["fallback_triggered"] is True

        monkeypatch.setattr(env, "graphify_fallback_to_cpu", False)
        strict_cuda = GraphGPUAccelerator.resolve_compute_backend("cuda:1")
        assert strict_cuda["backend"] == "torch_cuda"
        assert strict_cuda["device"] == "cuda:1"
        assert strict_cuda["use_gpu"] is True

        # Mock Apple Silicon MPS available
        mps_hw = {**cpu_hw, "torch_available": True, "torch_mps": True}
        monkeypatch.setattr(ga_mod, "_detect_compute_hardware", lambda: dict(mps_hw))
        res_mps = GraphGPUAccelerator.resolve_compute_backend("mps")
        assert res_mps["backend"] == "torch_mps"
        assert res_mps["device"] == "mps"
        assert res_mps["use_gpu"] is True

        # Mock cuGraph + CUDA available
        cu_hw = {**cpu_hw, "torch_available": True, "torch_cuda": True, "cuda_available": True, "cugraph_available": True}
        monkeypatch.setattr(ga_mod, "_detect_compute_hardware", lambda: dict(cu_hw))
        res_cu = GraphGPUAccelerator.resolve_compute_backend("auto")
        assert res_cu["backend"] == "cugraph_cuda"
        assert res_cu["device"] == "cuda:0"
        assert res_cu["use_gpu"] is True

    def test_cosine_similarity_topk_accuracy_and_gpu_oom_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import projectbrain.graphify_engine.gpu_accelerator as ga_mod

        rng = np.random.default_rng(12345)
        mat_a = rng.normal(size=(6, 16)).astype(np.float32)
        mat_b = rng.normal(size=(5, 16)).astype(np.float32)

        # Exact reference cosine similarity
        na = mat_a / np.linalg.norm(mat_a, axis=1, keepdims=True)
        nb = mat_b / np.linalg.norm(mat_b, axis=1, keepdims=True)
        exact = na @ nb.T
        expected_pairs: List[Tuple[int, int, float]] = []
        for i in range(6):
            order = np.argsort(-exact[i])[:2]
            for j in order:
                if exact[i, j] >= 0.1:
                    expected_pairs.append((i, int(j), float(exact[i, j])))
        expected_pairs.sort(key=lambda item: (-item[2], item[0], item[1]))

        pairs_cpu, meta_cpu = GraphGPUAccelerator.cosine_similarity_topk(
            mat_a, mat_b, top_k=2, min_score=0.1, device="cpu"
        )
        assert meta_cpu["backend_used"] == "numpy_blas_cpu"
        assert meta_cpu["fallback_triggered"] is False
        assert len(pairs_cpu) == len(expected_pairs)
        for (ai, aj, asc), (ei, ej, esc) in zip(pairs_cpu, expected_pairs):
            assert (ai, aj) == (ei, ej)
            assert asc == pytest.approx(esc, abs=1e-5)

        # Simulate PyTorch GPU raising RuntimeError("CUDA out of memory") -> fallback to NumPy BLAS
        cuda_hw = {
            "torch_available": True,
            "torch_cuda": True,
            "torch_mps": False,
            "cuda_available": True,
            "cugraph_available": False,
            "igraph_available": False,
            "scipy_available": False,
            "networkx_available": True,
            "graspologic_available": False,
        }
        monkeypatch.setattr(ga_mod, "_detect_compute_hardware", lambda: dict(cuda_hw))
        monkeypatch.setattr(env, "graphify_fallback_to_cpu", True)

        fake_torch = types.ModuleType("torch")
        fake_torch.float32 = "float32"
        fake_torch.device = lambda d: d

        class _FakeTensor:
            def to(self, *args, **kwargs):
                raise RuntimeError("CUDA out of memory")

        fake_torch.from_numpy = lambda arr: _FakeTensor()
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        pairs_fb, meta_fb = GraphGPUAccelerator.cosine_similarity_topk(
            mat_a, mat_b, top_k=2, min_score=0.1, device="cuda"
        )
        assert meta_fb["backend_used"] == "numpy_blas_cpu"
        assert meta_fb["device_used"] == "cpu"
        assert meta_fb["fallback_triggered"] is True
        assert "CUDA out of memory" in (meta_fb["fallback_error"] or "")
        assert [(a, b) for a, b, _ in pairs_fb] == [(a, b) for a, b, _ in pairs_cpu]

    def test_embed_texts_tfidf_and_mocked_local_and_st_backends(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        texts = [
            "UART DMA ring buffer interrupt handler",
            "BLE GATT advertising pairing flow",
        ]

        # 1. TF-IDF 256-dim deterministic unit vectors
        vecs_tfidf, meta_tfidf = GraphGPUAccelerator.embed_texts(
            texts, engine="tfidf", device="cpu"
        )
        assert vecs_tfidf.shape == (2, 256)
        assert meta_tfidf["engine_used"] == "tfidf_ngram_projection"
        assert np.allclose(np.linalg.norm(vecs_tfidf, axis=1), 1.0, atol=1e-5)

        # 2. Mocked Ollama (/api/embed) and vLLM (/v1/embeddings) via httpx.MockTransport
        def mock_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/api/embed"):
                return httpx.Response(
                    200,
                    json={"embeddings": [[3.0, 4.0, 0.0, 0.0], [0.0, 0.0, 5.0, 12.0]]},
                )
            if url.endswith("/v1/embeddings"):
                assert request.headers.get("authorization") == "Bearer local-no-key"
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"index": 1, "embedding": [0.0, 0.0, 1.0, 0.0]},
                            {"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]},
                        ]
                    },
                )
            return httpx.Response(404, text="not found")

        transport = httpx.MockTransport(mock_handler)
        orig_client_cls = httpx.Client
        monkeypatch.setattr(env, "local_ai_api_key", "")
        monkeypatch.setattr(
            "projectbrain.graphify_engine.gpu_accelerator.httpx.Client",
            lambda *args, **kwargs: orig_client_cls(transport=transport),
        )

        vecs_ol, meta_ol = GraphGPUAccelerator.embed_texts(
            texts, engine="ollama", local_ai_url="http://localhost:11434", model="nomic-embed-text"
        )
        assert meta_ol["engine_used"] == "ollama"
        assert vecs_ol.shape == (2, 4)
        assert np.allclose(vecs_ol[0], [0.6, 0.8, 0.0, 0.0], atol=1e-5)

        vecs_vllm, meta_vllm = GraphGPUAccelerator.embed_texts(
            texts, engine="vllm", model="BAAI/bge-small-en-v1.5"
        )
        assert meta_vllm["engine_used"] == "vllm"
        assert np.allclose(vecs_vllm[0], [1.0, 0.0, 0.0, 0.0], atol=1e-5)
        assert np.allclose(vecs_vllm[1], [0.0, 0.0, 1.0, 0.0], atol=1e-5)

        # 3. Mocked sentence_transformers with GPU->CPU fallback
        import projectbrain.graphify_engine.gpu_accelerator as ga_mod

        fake_st_mod = types.ModuleType("sentence_transformers")
        calls: List[str] = []

        class _FakeSentenceTransformer:
            def __init__(self, model_name: str, device: str = "cpu") -> None:
                self.device = device
                calls.append(device)
                if device != "cpu":
                    raise RuntimeError("CUDA out of memory during model load")

            def encode(self, items, **kwargs):
                return np.ones((len(items), 8), dtype=np.float32)

        fake_st_mod.SentenceTransformer = _FakeSentenceTransformer
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st_mod)
        GraphGPUAccelerator._st_model_cache.clear()
        monkeypatch.setattr(
            ga_mod,
            "_detect_compute_hardware",
            lambda: {
                "torch_available": True,
                "torch_cuda": True,
                "torch_mps": False,
                "cuda_available": True,
                "cugraph_available": False,
                "igraph_available": False,
                "scipy_available": False,
                "networkx_available": True,
                "graspologic_available": False,
            },
        )
        monkeypatch.setattr(env, "graphify_fallback_to_cpu", True)

        vecs_st, meta_st = GraphGPUAccelerator.embed_texts(
            texts, engine="sentence_transformers", device="cuda", model="fake/bge-mini"
        )
        assert meta_st["engine_used"] == "sentence_transformers"
        assert meta_st["device_used"] == "cpu"
        assert meta_st["fallback_triggered"] is True
        assert calls == ["cuda:0", "cpu"]
        assert vecs_st.shape == (2, 8)
        GraphGPUAccelerator._st_model_cache.clear()

    def test_accelerated_pagerank_and_clustering_on_networkx_graph(self) -> None:
        # Build a two-community NetworkX graph with a clear hub
        g = nx.DiGraph()
        g.add_edges_from(
            [
                ("a1", "hub"),
                ("a2", "hub"),
                ("a3", "hub"),
                ("hub", "a1"),
                ("a1", "a2"),
                ("b1", "b2"),
                ("b2", "b3"),
                ("b3", "b1"),
                ("b1", "hub"),
                ("hub", "b1"),
            ]
        )
        node_ids = list(g.nodes())
        edge_tuples = [(u, v, 1.0) for u, v in g.edges()]

        pr_res = GraphGPUAccelerator.accelerated_pagerank_and_centrality(
            node_ids, edge_tuples, device="cpu"
        )
        assert set(pr_res["pagerank"].keys()) == set(node_ids)
        assert sum(pr_res["pagerank"].values()) == pytest.approx(1.0, abs=1e-4)
        assert max(pr_res["pagerank"], key=pr_res["pagerank"].get) == "hub"
        assert pr_res["in_degree"]["hub"] == 4
        assert pr_res["out_degree"]["hub"] == 2
        assert pr_res["degree_centrality"]["hub"] == pytest.approx(6.0 / (len(node_ids) - 1))

        cl_res = GraphGPUAccelerator.accelerated_clustering(
            node_ids, edge_tuples, method="louvain", device="cpu"
        )
        assert cl_res["cluster_count"] >= 2
        assert set(cl_res["clusters"].keys()) == set(node_ids)
        assert cl_res["modularity"] > 0.0
        assert len(cl_res["communities"]) == cl_res["cluster_count"]
        assert all(0.0 <= c["cohesion"] <= 1.0 for c in cl_res["communities"])

    def test_parallel_graphify_extractor_cache_and_semantic_edges(
        self, tmp_path: Path
    ) -> None:
        ws = tmp_path / "repo"
        ws.mkdir()
        for idx in range(5):
            (ws / f"ble_module_{idx}.py").write_text(
                f'"""BLE pairing controller module {idx}."""\n'
                f"class BlePairingService{idx}:\n"
                f"    def pair_ble_device_{idx}(self, mac: str) -> bool:\n"
                f"        return bool(mac)\n",
                encoding="utf-8",
            )
        (ws / "ble_guide.md").write_text(
            "# BLE Pairing Guide\n\nUses `BlePairingService0` and `pair_ble_device_0`.\n",
            encoding="utf-8",
        )

        cache_dir = tmp_path / "cache"
        extractor = GraphifyExtractor(
            cache_dir=cache_dir, workers=4, device="cpu", semantic_engine="tfidf"
        )

        delta1 = extractor.extract_workspace(
            workspace_path=ws,
            project_id="proj-par",
            branch="main",
            use_cache=True,
            device="cpu",
            workers=4,
            semantic_engine="tfidf",
        )
        assert len(delta1.nodes) >= 6
        assert delta1.acceleration_meta["uncached_files"] == 6
        assert delta1.acceleration_meta["workers_used"] > 1

        # Second run hits SHA256 cache for all 6 files
        delta2 = extractor.extract_workspace(
            workspace_path=ws,
            project_id="proj-par",
            branch="main",
            use_cache=True,
            device="cpu",
            workers=4,
            semantic_engine="tfidf",
        )
        assert delta2.acceleration_meta["uncached_files"] == 0
        assert {n.id for n in delta2.nodes} == {n.id for n in delta1.nodes}

        # Semantic similarity pass emits INFERRED edges
        before_edges = len(delta2.edges)
        delta_sem = extractor.compute_semantic_edges(
            delta2, top_k=3, min_similarity=0.1, device="cpu", semantic_engine="tfidf"
        )
        sem_edges = [e for e in delta_sem.edges if e.relation == "semantically_similar_to"]
        assert len(delta_sem.edges) > before_edges
        assert len(sem_edges) >= 1
        assert all(e.confidence == "INFERRED" for e in sem_edges)

    def test_code_doc_linker_accelerated_semantic_cross_links(
        self, tmp_path: Path
    ) -> None:
        ws = tmp_path / "linker_ws"
        src_dir = ws / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "uart_driver.py").write_text(
            "def uart_watchdog_recovery(baud_rate: int) -> bool:\n"
            '    """Recover UART controller after watchdog timeout reset."""\n'
            "    return baud_rate > 0\n",
            encoding="utf-8",
        )

        linker = CodeDocLinker(workspace_path=ws, device="cpu")
        symbol_index = linker.build_symbol_index(GraphDelta(project_id="p1", branch="main"))

        # Chunk without backticks so lexical/semantic similarity drives INFERRED cross-link
        chunk = DocChunk(
            chunk_id="chunk_uart_1",
            doc_node_id="doc:uart.md",
            doc_path="docs/uart.md",
            heading_breadcrumb="UART Watchdog Recovery",
            level=1,
            start_line=1,
            end_line=5,
            content="Recover UART controller after watchdog timeout reset using uart_watchdog_recovery baud_rate.",
            sha256="abc123",
            tokens=12,
        )
        edges, _drift = linker.link_chunks_to_code(
            [chunk],
            symbol_index,
            GraphDelta(project_id="p1", branch="main"),
            device="cpu",
            semantic_top_k=3,
            min_similarity=0.3,
        )

        inferred = [
            e
            for e in edges
            if e.relation == "conceptually_related_to" and e.confidence == "INFERRED"
        ]
        assert len(inferred) >= 1
        assert any("uart_watchdog_recovery" in (e.metadata.get("symbol_name") or "") for e in inferred)

        # Also verify GraphGPUAccelerator semantic path when device="auto" (TF-IDF projection)
        edges_acc, _ = linker.link_chunks_to_code(
            [chunk],
            symbol_index,
            GraphDelta(project_id="p1", branch="main"),
            device="auto",
            semantic_top_k=3,
            min_similarity=0.3,
        )
        inferred_acc = [
            e
            for e in edges_acc
            if e.relation == "conceptually_related_to" and e.confidence == "INFERRED"
        ]
        assert len(inferred_acc) >= 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "provider",
        ["vllm", "lmstudio", "llamacpp", "local_gpu"],
    )
    async def test_memory_embed_dispatch_supports_local_ai_and_local_gpu(
        self,
        provider: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded: List[Dict[str, Any]] = []

        def fake_embed_texts(
            texts: List[str],
            engine: str = "auto",
            device: str = "auto",
            batch_size: int = 64,
            local_ai_url: Optional[str] = None,
            model: Optional[str] = None,
            allow_fallback: bool = True,
        ):
            recorded.append(
                {
                    "texts": texts,
                    "engine": engine,
                    "device": device,
                    "local_ai_url": local_ai_url,
                    "model": model,
                    "allow_fallback": allow_fallback,
                }
            )
            return np.asarray([[0.25, 0.5, 0.75, 0.35]], dtype=np.float32), {"engine_used": engine}

        monkeypatch.setattr(env, "embed_device", "cpu")
        monkeypatch.setattr(env, "local_embed_model", "BAAI/bge-small-en-v1.5")
        monkeypatch.setattr(env, "local_ai_model", "nomic-local")
        monkeypatch.setattr(env, "local_ai_base_url", "http://localhost:8000/v1")
        monkeypatch.setattr(GraphGPUAccelerator, "embed_texts", classmethod(lambda cls, *a, **kw: fake_embed_texts(*a, **kw)))

        vec = await emb_dispatch(provider, "hello embedded world", "semantic")
        assert len(vec) == 4
        assert vec == pytest.approx([0.25, 0.5, 0.75, 0.35], abs=1e-5)
        assert len(recorded) == 1
        expected_engine = "gpu_embed" if provider == "local_gpu" else provider
        assert recorded[0]["engine"] == expected_engine
        assert recorded[0]["device"] == "cpu"


# ---------------------------------------------------------------------------
# 4. End-to-End Hardware Status (REST, MCP, RBAC, CLI) & GPU/MPS Guards
# ---------------------------------------------------------------------------
class TestHardwareStatusAndGPURemediation:
    def test_graphify_hardware_rest_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient
        from projectbrain.server.api import create_app

        monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")
        client = TestClient(create_app())

        resp = client.get(
            "/graphify/hardware",
            params={
                "ocr_device": "cpu",
                "graphify_device": "cpu",
                "semantic_engine": "tfidf",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["ocr"], dict)
        assert isinstance(data["graphify"], dict)
        assert data["ocr"]["resolved_device"]["device_str"] == "cpu"
        assert data["ocr"]["resolved_device"]["use_gpu"] is False
        assert data["ocr"]["config"]["ocr_device"] == "cpu"
        assert data["graphify"]["resolved_backend"]["backend"] == "numpy_blas_cpu"
        assert data["graphify"]["resolved_backend"]["device"] == "cpu"
        assert data["graphify"]["config"]["graphify_device"] == "cpu"
        assert data["graphify"]["config"]["graphify_semantic_engine"] == "tfidf"

    @pytest.mark.asyncio
    async def test_mcp_projectbrain_hardware_status_and_rbac(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from projectbrain.ai.mcp import mcp_server, projectbrain_hardware_status
        from projectbrain.server.api import _is_write_tool

        monkeypatch.delenv("PB_REMOTE_URL", raising=False)
        monkeypatch.setenv("PB_MODE", "local")

        tools = mcp_server._tool_manager.list_tools()
        tool_map = {t.name: t for t in tools}
        assert "projectbrain_hardware_status" in tool_map

        assert _is_write_tool("projectbrain_hardware_status") is False
        assert _is_write_tool("projectbrain_hardware_status", tool_map["projectbrain_hardware_status"]) is False

        res = await projectbrain_hardware_status(
            ocr_device="cpu",
            graphify_device="cpu",
            semantic_engine="tfidf",
        )
        assert res["status"] == "ok"
        assert isinstance(res["ocr"], dict)
        assert isinstance(res["graphify"], dict)
        assert res["ocr"]["resolved_device"]["device_str"] == "cpu"
        assert res["ocr"]["resolved_device"]["use_gpu"] is False
        assert res["graphify"]["resolved_backend"]["backend"] == "numpy_blas_cpu"

    def test_cli_hardware_status_run_and_subprocess(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import os
        import subprocess
        from projectbrain.main import run_hardware_status

        res = run_hardware_status(
            device="cpu",
            ocr_device="cpu",
            semantic_engine="tfidf",
        )
        assert res["status"] == "ok"
        assert isinstance(res["ocr"], dict)
        assert isinstance(res["graphify"], dict)
        captured = capsys.readouterr()
        parsed_direct = json.loads(captured.out)
        assert parsed_direct["status"] == "ok"
        assert parsed_direct["graphify"]["resolved_backend"]["backend"] == "numpy_blas_cpu"

        repo_root = Path(__file__).resolve().parents[1]
        src_dir = repo_root / "src"
        cli_env = os.environ.copy()
        existing_pp = cli_env.get("PYTHONPATH", "")
        cli_env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else str(src_dir)

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "projectbrain.main",
                "hardware-status",
                "--device",
                "cpu",
                "--ocr-device",
                "cpu",
                "--semantic-engine",
                "tfidf",
            ],
            cwd=str(tmp_path),
            env=cli_env,
            capture_output=True,
            text=True,
            check=True,
        )
        parsed_cli = json.loads(proc.stdout)
        assert parsed_cli["status"] == "ok"
        assert isinstance(parsed_cli["ocr"], dict)
        assert isinstance(parsed_cli["graphify"], dict)
        assert parsed_cli["ocr"]["resolved_device"]["device_str"] == "cpu"
        assert parsed_cli["ocr"]["resolved_device"]["use_gpu"] is False
        assert parsed_cli["graphify"]["resolved_backend"]["backend"] == "numpy_blas_cpu"
        assert parsed_cli["graphify"]["config"]["graphify_semantic_engine"] == "tfidf"

    def test_gpu_strict_fallback_disabled_raises_and_mps_skips_sparse_coo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import projectbrain.graphify_engine.gpu_accelerator as ga_mod

        no_gpu_hw = {
            "torch_available": False,
            "torch_cuda": False,
            "torch_mps": False,
            "cuda_available": False,
            "cugraph_available": False,
            "igraph_available": False,
            "scipy_available": False,
            "networkx_available": True,
            "graspologic_available": False,
        }
        monkeypatch.setattr(ga_mod, "_detect_compute_hardware", lambda: dict(no_gpu_hw))
        monkeypatch.setattr(env, "graphify_fallback_to_cpu", False)

        with pytest.raises(RuntimeError, match="neither CUDA nor Apple MPS is available"):
            GraphGPUAccelerator.resolve_compute_backend(device="gpu")

        node_ids = ["n1", "n2", "n3", "n4"]
        edges = [("n1", "n2", 1.0), ("n2", "n3", 1.0), ("n3", "n4", 1.0), ("n4", "n1", 1.0)]

        with pytest.raises(RuntimeError, match="neither CUDA nor Apple MPS is available"):
            GraphGPUAccelerator.accelerated_pagerank_and_centrality(
                node_ids, edges, device="gpu"
            )

        with pytest.raises(RuntimeError, match="neither CUDA nor Apple MPS is available"):
            GraphGPUAccelerator.accelerated_clustering(
                node_ids, edges, device="gpu"
            )

        # Verify torch_mps skips PyTorch sparse COO ops without raising NotImplementedError
        mps_hw = {
            **no_gpu_hw,
            "torch_available": True,
            "torch_mps": True,
            "mps_available": True,
        }
        monkeypatch.setattr(ga_mod, "_detect_compute_hardware", lambda: dict(mps_hw))
        monkeypatch.setattr(env, "graphify_fallback_to_cpu", True)

        sparse_calls: List[str] = []

        def _forbidden_sparse(*args: Any, **kwargs: Any) -> Any:
            sparse_calls.append("called")
            raise NotImplementedError("aten::_sparse_coo_tensor_with_dims_and_tensors is not implemented on MPS")

        fake_torch = types.SimpleNamespace(
            sparse_coo_tensor=_forbidden_sparse,
            sparse=types.SimpleNamespace(mm=_forbidden_sparse),
            device=lambda d: d,
        )
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        pr_res = GraphGPUAccelerator.accelerated_pagerank_and_centrality(
            node_ids, edges, device="mps"
        )
        assert sparse_calls == []
        assert sum(pr_res["pagerank"].values()) == pytest.approx(1.0, abs=1e-4)
        assert pr_res["backend_used"] == "numpy_blas_cpu"

        cl_res = GraphGPUAccelerator.accelerated_clustering(
            node_ids, edges, method="leiden", device="mps"
        )
        assert sparse_calls == []
        assert set(cl_res["clusters"].keys()) == set(node_ids)
        assert cl_res["backend_used"] == "networkx_louvain_cpu"
