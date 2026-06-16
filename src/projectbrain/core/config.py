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
        self.ollama_embedding_model = pb_getenv("OM_OLLAMA_EMBEDDING_MODEL")
        self.gemini_embedding_model = pb_getenv("OM_GEMINI_EMBEDDING_MODEL")
        self.aws_embedding_model = pb_getenv("OM_AWS_EMBEDDING_MODEL")
        self.active_project = None
        
    @property
    def database_url(self) -> str:
        return self.db_url

    @database_url.setter
    def database_url(self, val: str):
        self.db_url = val
        if val.startswith("sqlite:///"):
            self.db_path = val.replace("sqlite:///", "")

env = EnvConfig()
