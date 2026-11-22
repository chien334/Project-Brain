import os
import sys
from pathlib import Path
from typing import Literal, List, Optional, Any
from dotenv import load_dotenv

msg_root = Path(__file__).parent.parent.parent.parent / ".env"
load_dotenv(msg_root)

# Proxy bypass setup to bypass corporate proxy for internal traffic
no_proxy_val = os.getenv("PB_NO_PROXY") or os.getenv("OM_NO_PROXY") or os.getenv("NO_PROXY") or os.getenv("no_proxy")
if not no_proxy_val:
    no_proxy_val = "localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,5.104.85.38"
os.environ["NO_PROXY"] = no_proxy_val
os.environ["no_proxy"] = no_proxy_val

def num(v: Optional[str], d: int | float) -> int | float:
    try:
        return float(v) if v else d
    except ValueError:
        return d

def s_bool(v: Optional[str]) -> bool:
    return str(v).lower() == "true"

def s_str(v: Optional[str], d: str) -> str:
    return v if v else d

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

def pb_getenv(env_var: str, default: Any = None) -> Any:
    pb_var = env_var.replace("OM_", "PB_")
    val = os.getenv(pb_var)
    if val is not None:
        return val
    return os.getenv(env_var, default)

def detect_project_name() -> str:
    import re
    import json
    cwd = Path.cwd()
    # Check current directory and parents
    for parent in [cwd] + list(cwd.parents):
        # 1. pyproject.toml
        pyproject = parent / "pyproject.toml"
        if pyproject.exists():
            try:
                with open(pyproject, "r", encoding="utf-8") as f:
                    content = f.read()
                # Find name="..." inside the file
                matches = re.findall(r'(?:^|\n)\s*name\s*=\s*["\']([^"\']+)["\']', content)
                if matches:
                    return matches[0]
            except Exception:
                pass

        # 2. package.json
        pkg_json = parent / "package.json"
        if pkg_json.exists():
            try:
                with open(pkg_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "name" in data and isinstance(data["name"], str):
                        return data["name"]
            except Exception:
                pass

        # 3. Cargo.toml
        cargo = parent / "Cargo.toml"
        if cargo.exists():
            try:
                with open(cargo, "r", encoding="utf-8") as f:
                    content = f.read()
                matches = re.findall(r'(?:^|\n)\s*name\s*=\s*["\']([^"\']+)["\']', content)
                if matches:
                    return matches[0]
            except Exception:
                pass

    # Fallback to CWD basename
    basename = os.path.basename(os.getcwd())
    if not basename or basename in ["", "/", "\\"]:
        return "projectbrain"
    return basename

class EnvConfig:
    def __init__(self):
        self._toml = {}
        toml_path = Path("projectbrain.toml")
        if not toml_path.exists():
            toml_path = Path("projectbrain.toml")
            
        if tomllib and toml_path.exists():
            with open(toml_path, "rb") as f:
                self._toml = tomllib.load(f)
                
        def get(sec: str, key: str, env_var: str, default: Any) -> Any:
            val = self._toml.get(sec, {}).get(key)
            if val is not None: return val
            return pb_getenv(env_var, default)
            
        self.db_backend = get("db", "backend", "OM_METADATA_BACKEND", "sqlite")
        self.pg_host = get("db", "pg_host", "OM_PG_HOST", "localhost")
        self.pg_port = get("db", "pg_port", "OM_PG_PORT", "5432")
        self.pg_db = get("db", "pg_db", "OM_PG_DB", "projectbrain")
        self.pg_user = get("db", "pg_user", "OM_PG_USER", "postgres")
        self.pg_pass = get("db", "pg_pass", "OM_PG_PASSWORD", "postgres")

        self.db_url = get("db", "url", "OM_DB_URL", "")
        if not self.db_url:
            if self.db_backend == "postgres":
                self.db_url = f"postgresql://{self.pg_user}:{self.pg_pass}@{self.pg_host}:{self.pg_port}/{self.pg_db}"
            else:
                db_file = pb_getenv("OM_DB_PATH")
                if db_file:
                    self.db_url = f"sqlite:///{db_file}"
                else:
                    project_name = detect_project_name()
                    clean_name = "".join(c for c in project_name if c.isalnum() or c in "-_").lower()
                    if not clean_name:
                        clean_name = "projectbrain"
                    self.db_url = f"sqlite:///{clean_name}.db"

        if self.db_url.startswith("sqlite:///"):
            self.db_path = self.db_url.replace("sqlite:///", "")
        else:
            default_db_path = str(Path(__file__).parent.parent.parent.parent / "data" / "projectbrain.sqlite")
            self.db_path = s_str(pb_getenv("OM_DB_PATH"), default_db_path)
            
        self.max_context_items = int(get("context", "max_items", "OM_MAX_CONTEXT_ITEMS", 16))
        self.max_context_tokens = int(get("context", "max_tokens", "OM_MAX_CONTEXT_TOKENS", 2048))
        self.decay_half_life = float(get("decay", "half_life_days", "OM_DECAY_HALF_LIFE", 14))
        self.decay_lambda = num(pb_getenv("OM_DECAY_LAMBDA"), 0.02)
        self.openai_key = get("ai", "openai_key", "OPENAI_API_KEY", "") or pb_getenv("OM_OPENAI_API_KEY")
        self.openai_base_url = get("ai", "openai_base", "OM_OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.openai_model = get("ai", "openai_model", "OM_OPENAI_MODEL", None)

        self.ollama_url = get("ai", "ollama_url", "OLLAMA_URL", "http://localhost:11434")
        self.ollama_model = (
            get("ai", "ollama_model", "OM_OLLAMA_MODEL", None)
            or os.getenv("OLLAMA_MODEL")
            or "llama3.2"
        )
        self.ollama_vision_model = (
            get("ai", "ollama_vision_model", "PB_OLLAMA_VISION_MODEL", None)
            or os.getenv("OLLAMA_VISION_MODEL")
            or "qwen2.5vl:7b"
        )
        self.local_ai_base_url = str(
            get("ai", "local_ai_base_url", "PB_LOCAL_AI_URL", "") or ""
        ).strip()
        self.local_ai_model = str(
            get("ai", "local_ai_model", "PB_LOCAL_AI_MODEL", "") or ""
        ).strip()
        self.local_ai_api_key = (
            str(get("ai", "local_ai_api_key", "PB_LOCAL_AI_API_KEY", "") or "").strip()
            or "local-no-key"
        )

        self.emb_kind = get("ai", "embedding_provider", "OM_EMBED_KIND", "synthetic")
        self.gemini_key = get("ai", "gemini_key", "GEMINI_API_KEY",  pb_getenv("OM_GEMINI_KEY"))
        self.gemini_model = get("ai", "gemini_model", "OM_GEMINI_MODEL", "gemini-3.1-pro-preview")
        self.gemini_base_url = get("ai", "gemini_base_url", "OM_GEMINI_BASE_URL", "")
        self.aws_region = get("ai", "aws_region", "AWS_REGION", None)
        self.aws_access_key_id = get("ai", "aws_access_key_id", "AWS_ACCESS_KEY_ID", None)
        self.aws_secret_access_key = get("ai", "aws_secret_access_key", "AWS_SECRET_ACCESS_KEY", None)

        self.siray_key = get("ai", "siray_key", "SIRAY_API_TOKEN", "") or pb_getenv("OM_SIRAY_API_TOKEN")
        self.siray_base_url = get("ai", "siray_base", "OM_SIRAY_BASE_URL", "https://api.siray.ai/v1")
        self.siray_model = get("ai", "siray_model", "OM_SIRAY_MODEL", None)

        self.minimax_key = get("ai", "minimax_key", "MINIMAX_API_KEY", "") or pb_getenv("OM_MINIMAX_API_KEY")
        self.minimax_base_url = get("ai", "minimax_base", "OM_MINIMAX_BASE_URL", "https://api.minimax.io/v1")
        self.minimax_model = get("ai", "minimax_model", "OM_MINIMAX_MODEL", None)
        self.minimax_embedding_model = pb_getenv("OM_MINIMAX_EMBEDDING_MODEL")

        self.vec_dim = int(num(pb_getenv("OM_VEC_DIM"), 1536))
        self.min_score = num(pb_getenv("OM_MIN_SCORE"), 0.3)
        self.keyword_boost = num(pb_getenv("OM_KEYWORD_BOOST"), 2.5)
        self.seg_size = int(num(pb_getenv("OM_SEG_SIZE"), 10000))

        self.decay_threads = int(num(pb_getenv("OM_DECAY_THREADS"), 3))
        self.decay_cold_threshold = num(pb_getenv("OM_DECAY_COLD_THRESHOLD"), 0.25)
        self.max_vector_dim = int(num(pb_getenv("OM_MAX_VECTOR_DIM"), 1536))
        self.min_vector_dim = int(num(pb_getenv("OM_MIN_VECTOR_DIM"), 64))
        self.summary_layers = int(num(pb_getenv("OM_SUMMARY_LAYERS"), 3))
        self.decay_ratio = num(pb_getenv("OM_DECAY_RATIO"), 0.03)
        self.embed_delay_ms = int(num(pb_getenv("OM_EMBED_DELAY_MS"), 0))
        self.use_summary_only = s_bool(pb_getenv("OM_USE_SUMMARY_ONLY"))
        self.summary_max_length = int(num(pb_getenv("OM_SUMMARY_MAX_LENGTH"), 1000))
        self.rate_limit_enabled = s_bool(pb_getenv("OM_RATE_LIMIT_ENABLED"))
        self.rate_limit_window_ms = int(num(pb_getenv("OM_RATE_LIMIT_WINDOW_MS"), 60000))
        self.rate_limit_max_requests = int(num(pb_getenv("OM_RATE_LIMIT_MAX"), 100))
        self.keyword_min_length = int(num(pb_getenv("OM_KEYWORD_MIN_LENGTH"), 3))
        self.user_summary_interval = int(num(pb_getenv("OM_USER_SUMMARY_INTERVAL"), 30))
        self.auto_reflect = s_bool(pb_getenv("OM_AUTO_REFLECT", "true"))
        self.reflect_interval = int(num(pb_getenv("OM_REFLECT_INTERVAL"), 10))
        self.reflect_min = int(num(pb_getenv("OM_REFLECT_MIN_MEMORIES"), 20))
        self.ollama_embedding_model = pb_getenv("OM_OLLAMA_EMBEDDING_MODEL")
        self.gemini_embedding_model = pb_getenv("OM_GEMINI_EMBEDDING_MODEL")
        self.aws_embedding_model = pb_getenv("OM_AWS_EMBEDDING_MODEL")
        self.root_branch = get("graph", "root_branch", "PB_ROOT_BRANCH", "") or pb_getenv("OM_ROOT_BRANCH") or "main"
        self.active_project = None

        # Plugin Microkernel configuration (projectbrain.toml [plugins] & PB_PLUGINS_* env vars)
        plugins_sec = self._toml.get("plugins", {})
        if not isinstance(plugins_sec, dict):
            plugins_sec = {}

        def _parse_plugin_list(raw: Any) -> List[str]:
            if raw is None:
                return []
            if isinstance(raw, str):
                return [item.strip() for item in raw.split(",") if item.strip()]
            if isinstance(raw, (list, tuple, set)):
                return [str(item).strip() for item in raw if str(item).strip()]
            return []

        raw_plugins_dir = plugins_sec.get("dir")
        if raw_plugins_dir is None:
            raw_plugins_dir = plugins_sec.get("plugins_dir")
        if (
            raw_plugins_dir is None
            and isinstance(plugins_sec.get("directories"), list)
            and plugins_sec["directories"]
        ):
            raw_plugins_dir = plugins_sec["directories"][0]
        if raw_plugins_dir is not None:
            self.plugins_dir: str = str(raw_plugins_dir)
        else:
            self.plugins_dir = s_str(
                pb_getenv("PB_PLUGINS_DIR") or pb_getenv("OM_PLUGINS_DIR"),
                "./plugins",
            )

        raw_plugins_cfg = plugins_sec.get("config_path")
        if raw_plugins_cfg is None:
            raw_plugins_cfg = plugins_sec.get("plugins_config_path")
        if raw_plugins_cfg is not None:
            self.plugins_config_path: Optional[str] = str(raw_plugins_cfg)
        else:
            cfg_env = (
                pb_getenv("PB_PLUGINS_CONFIG_PATH")
                or pb_getenv("PB_PLUGINS_CONFIG")
                or pb_getenv("OM_PLUGINS_CONFIG_PATH")
            )
            self.plugins_config_path = str(cfg_env) if cfg_env else None

        raw_enabled = plugins_sec.get("enabled")
        if raw_enabled is None:
            raw_enabled = plugins_sec.get("enabled_plugins")
        if raw_enabled is not None:
            self.enabled_plugins: List[str] = _parse_plugin_list(raw_enabled)
        else:
            merged_enabled: List[str] = []
            for env_key in ("PB_PLUGINS_ENABLED", "PB_ENABLED_PLUGINS"):
                for item in _parse_plugin_list(pb_getenv(env_key)):
                    if item not in merged_enabled:
                        merged_enabled.append(item)
            self.enabled_plugins = merged_enabled

        raw_disabled = plugins_sec.get("disabled")
        if raw_disabled is None:
            raw_disabled = plugins_sec.get("disabled_plugins")
        if raw_disabled is not None:
            self.disabled_plugins: List[str] = _parse_plugin_list(raw_disabled)
        else:
            merged_disabled: List[str] = []
            for env_key in ("PB_PLUGINS_DISABLED", "PB_DISABLED_PLUGINS"):
                for item in _parse_plugin_list(pb_getenv(env_key)):
                    if item not in merged_disabled:
                        merged_disabled.append(item)
            self.disabled_plugins = merged_disabled

        # OCR, Hardware & Local AI configuration (projectbrain.toml [ocr] & PB_OCR_* / OCR_* env vars)
        ocr_sec = self._toml.get("ocr", {})
        if not isinstance(ocr_sec, dict):
            ocr_sec = {}

        def _parse_bool(raw: Any, default: bool) -> bool:
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                return default
            if isinstance(raw, bool):
                return raw
            s = str(raw).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off"):
                return False
            return default

        def _parse_int(raw: Any, default: int) -> int:
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                return default
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        raw_ocr_engine = (
            ocr_sec.get("engine")
            or os.getenv("PB_OCR_ENGINE")
            or os.getenv("OCR_ENGINE")
            or "auto"
        )
        self.ocr_engine: str = str(raw_ocr_engine).strip().lower() or "auto"

        raw_ocr_device = (
            ocr_sec.get("device")
            or os.getenv("PB_OCR_DEVICE")
            or os.getenv("OCR_DEVICE")
        )
        if raw_ocr_device is not None and str(raw_ocr_device).strip():
            self.ocr_device: str = str(raw_ocr_device).strip().lower()
        else:
            legacy_paddle_gpu = os.getenv("OCR_PADDLE_USE_GPU")
            if legacy_paddle_gpu is not None and legacy_paddle_gpu.strip():
                self.ocr_device = "gpu" if _parse_bool(legacy_paddle_gpu, False) else "cpu"
            else:
                self.ocr_device = "auto"

        self.ocr_gpu_id: int = max(
            0,
            _parse_int(
                ocr_sec.get("gpu_id")
                if ocr_sec.get("gpu_id") is not None
                else (os.getenv("PB_OCR_GPU_ID") or os.getenv("OCR_GPU_ID")),
                0,
            ),
        )
        self.ocr_gpu_mem_mb: int = max(
            128,
            _parse_int(
                ocr_sec.get("gpu_mem_mb")
                if ocr_sec.get("gpu_mem_mb") is not None
                else os.getenv("PB_OCR_GPU_MEM_MB"),
                2000,
            ),
        )
        self.ocr_use_tensorrt: bool = _parse_bool(
            ocr_sec.get("use_tensorrt")
            if ocr_sec.get("use_tensorrt") is not None
            else os.getenv("PB_OCR_USE_TENSORRT"),
            False,
        )
        raw_precision = str(
            ocr_sec.get("precision")
            or os.getenv("PB_OCR_PRECISION")
            or "fp32"
        ).strip().lower()
        self.ocr_precision: str = (
            raw_precision if raw_precision in ("fp32", "fp16", "int8") else "fp32"
        )
        default_threads = min(8, os.cpu_count() or 4)
        self.ocr_cpu_threads: int = max(
            1,
            _parse_int(
                ocr_sec.get("cpu_threads")
                if ocr_sec.get("cpu_threads") is not None
                else os.getenv("PB_OCR_CPU_THREADS"),
                default_threads,
            ),
        )
        raw_mkldnn = (
            ocr_sec.get("enable_mkldnn")
            if ocr_sec.get("enable_mkldnn") is not None
            else os.getenv("PB_OCR_ENABLE_MKLDNN")
        )
        if raw_mkldnn is None or (isinstance(raw_mkldnn, str) and not raw_mkldnn.strip()):
            self.ocr_enable_mkldnn: str = "auto"
        elif isinstance(raw_mkldnn, bool):
            self.ocr_enable_mkldnn = "true" if raw_mkldnn else "false"
        else:
            mk_str = str(raw_mkldnn).strip().lower()
            if mk_str in ("1", "true", "yes", "on"):
                self.ocr_enable_mkldnn = "true"
            elif mk_str in ("0", "false", "no", "off"):
                self.ocr_enable_mkldnn = "false"
            else:
                self.ocr_enable_mkldnn = "auto"

        self.ocr_batch_size: int = max(
            1,
            _parse_int(
                ocr_sec.get("batch_size")
                if ocr_sec.get("batch_size") is not None
                else os.getenv("PB_OCR_BATCH_SIZE"),
                4,
            ),
        )
        self.ocr_fallback_to_cpu: bool = _parse_bool(
            ocr_sec.get("fallback_to_cpu")
            if ocr_sec.get("fallback_to_cpu") is not None
            else os.getenv("PB_OCR_FALLBACK_TO_CPU"),
            True,
        )
        self.ocr_paddle_lang: str = (
            str(
                ocr_sec.get("paddle_lang")
                or os.getenv("PB_OCR_PADDLE_LANG")
                or os.getenv("OCR_PADDLE_LANG")
                or "ch"
            ).strip()
            or "ch"
        )

        # Graphify & Embedding hardware acceleration (projectbrain.toml [graphify]/[ai] & PB_GRAPHIFY_* / PB_EMBED_*)
        graphify_sec = self._toml.get("graphify", {})
        if not isinstance(graphify_sec, dict):
            graphify_sec = {}
        ai_sec = self._toml.get("ai", {})
        if not isinstance(ai_sec, dict):
            ai_sec = {}

        raw_graphify_device = (
            graphify_sec.get("device")
            if graphify_sec.get("device") is not None
            else pb_getenv("PB_GRAPHIFY_DEVICE", "auto")
        )
        self.graphify_device: str = (
            str(raw_graphify_device or "auto").strip().lower() or "auto"
        )

        self.graphify_workers: int = max(
            1,
            _parse_int(
                graphify_sec.get("workers")
                if graphify_sec.get("workers") is not None
                else pb_getenv("PB_GRAPHIFY_WORKERS"),
                default_threads,
            ),
        )

        raw_graphify_semantic_engine = (
            graphify_sec.get("semantic_engine")
            if graphify_sec.get("semantic_engine") is not None
            else pb_getenv("PB_GRAPHIFY_SEMANTIC_ENGINE", "auto")
        )
        self.graphify_semantic_engine: str = (
            str(raw_graphify_semantic_engine or "auto").strip().lower() or "auto"
        )

        self.graphify_fallback_to_cpu: bool = _parse_bool(
            graphify_sec.get("fallback_to_cpu")
            if graphify_sec.get("fallback_to_cpu") is not None
            else pb_getenv("PB_GRAPHIFY_FALLBACK_TO_CPU"),
            True,
        )

        raw_embed_device = (
            ai_sec.get("embed_device")
            if ai_sec.get("embed_device") is not None
            else pb_getenv("PB_EMBED_DEVICE", "auto")
        )
        self.embed_device: str = (
            str(raw_embed_device or "auto").strip().lower() or "auto"
        )

        raw_local_embed_model = (
            ai_sec.get("local_embed_model")
            if ai_sec.get("local_embed_model") is not None
            else pb_getenv("PB_LOCAL_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
        )
        self.local_embed_model: str = (
            str(raw_local_embed_model or "BAAI/bge-small-en-v1.5").strip()
            or "BAAI/bge-small-en-v1.5"
        )

        # Identity & Access Management (projectbrain.toml [auth] & PB_* auth env vars)
        auth_sec = self._toml.get("auth", {})
        if not isinstance(auth_sec, dict):
            auth_sec = {}

        self.allow_registration: bool = _parse_bool(
            auth_sec.get("allow_registration")
            if auth_sec.get("allow_registration") is not None
            else pb_getenv("PB_ALLOW_REGISTRATION"),
            True,
        )
        raw_signup_role = str(
            auth_sec.get("default_signup_role")
            if auth_sec.get("default_signup_role") is not None
            else (pb_getenv("PB_DEFAULT_SIGNUP_ROLE") or "collaborator")
        ).strip().lower()
        self.default_signup_role: str = (
            raw_signup_role if raw_signup_role in ("collaborator", "reader") else "collaborator"
        )
        self.session_ttl_hours: int = max(
            1,
            _parse_int(
                auth_sec.get("session_ttl_hours")
                if auth_sec.get("session_ttl_hours") is not None
                else pb_getenv("PB_SESSION_TTL_HOURS"),
                24,
            ),
        )
        self.require_rest_auth: bool = _parse_bool(
            auth_sec.get("require_rest_auth")
            if auth_sec.get("require_rest_auth") is not None
            else pb_getenv("PB_REQUIRE_REST_AUTH"),
            False,
        )
        self.auth_lockout_max_attempts: int = max(
            1,
            _parse_int(
                auth_sec.get("lockout_max_attempts")
                if auth_sec.get("lockout_max_attempts") is not None
                else pb_getenv("PB_AUTH_LOCKOUT_MAX_ATTEMPTS"),
                5,
            ),
        )
        self.auth_lockout_seconds: int = max(
            1,
            _parse_int(
                auth_sec.get("lockout_seconds")
                if auth_sec.get("lockout_seconds") is not None
                else pb_getenv("PB_AUTH_LOCKOUT_SECONDS"),
                300,
            ),
        )

        # Multi-DB / Multi-Schema 3-tier tenant isolation (default True)
        db_sec = self._toml.get("db", {})
        if not isinstance(db_sec, dict):
            db_sec = {}
        self._initial_pb_db_path = os.getenv("PB_DB_PATH")
        self._initial_om_db_path = os.getenv("OM_DB_PATH")
        self._initial_db_url = self.db_url
        self._initial_multi_db_env = pb_getenv("OM_MULTI_DB_ENABLED")
        self.multi_db_enabled: bool = _parse_bool(
            db_sec.get("multi_db_enabled")
            if db_sec.get("multi_db_enabled") is not None
            else self._initial_multi_db_env,
            True,
        )
        
    @property
    def database_url(self) -> str:
        return self.db_url

    @database_url.setter
    def database_url(self, val: str):
        self.db_url = val
        if val.startswith("sqlite:///"):
            self.db_path = val.replace("sqlite:///", "")

env = EnvConfig()
