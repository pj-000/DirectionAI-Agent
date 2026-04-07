import os
from typing import Any

from dotenv import load_dotenv

from deerflow.config import get_app_config

load_dotenv()


def _normalize_openai_base_url(url: str) -> str:
    """
    OpenAI-compatible SDK expects a base URL like:
    https://openrouter.ai/api/v1
    rather than a full endpoint like:
    https://openrouter.ai/api/v1/chat/completions
    """
    normalized = (url or "").strip().rstrip("/")
    for suffix in (
        "/chat/completions",
        "/completions",
        "/responses",
    ):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _safe_get_app_config():
    try:
        return get_app_config()
    except Exception:
        return None


def _model_value(model_config: Any, key: str) -> Any:
    if model_config is None:
        return None
    direct = getattr(model_config, key, None)
    if direct not in (None, ""):
        return direct
    extras = getattr(model_config, "model_extra", None) or {}
    return extras.get(key)


MODEL_PROVIDER_ALIASES = {
    "minmax": ("minimax-m2.7",),
    "claude": ("claude-sonnet-4.6", "claude-sonnet-4.5", "claude"),
}

DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-M2.7"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _find_model_config(provider_or_name: str | None):
    app_config = _safe_get_app_config()
    if app_config is None or not getattr(app_config, "models", None):
        return None

    normalized = (provider_or_name or "").strip().lower()
    if not normalized:
        return app_config.models[0] if app_config.models else None

    for model in app_config.models:
        if str(getattr(model, "name", "")).strip().lower() == normalized:
            return model

    for alias in MODEL_PROVIDER_ALIASES.get(normalized, ()):
        for model in app_config.models:
            if str(getattr(model, "name", "")).strip().lower() == alias:
                return model

    for model in app_config.models:
        display_name = str(getattr(model, "display_name", "") or "").strip().lower()
        if normalized and normalized in display_name:
            return model

    return None


def _configured_provider_defaults(provider_or_name: str | None) -> dict[str, str]:
    model_config = _find_model_config(provider_or_name)
    if model_config is None:
        return {"api_key": "", "base_url": "", "model": ""}

    base_url = _normalize_openai_base_url(
        _first_non_empty(
            _model_value(model_config, "base_url"),
            _model_value(model_config, "openai_base_url"),
            _model_value(model_config, "openai_api_base"),
        )
    )
    return {
        "api_key": _first_non_empty(_model_value(model_config, "api_key")),
        "base_url": base_url,
        "model": _first_non_empty(getattr(model_config, "model", "")),
    }


_MINIMAX_CONFIG_DEFAULTS = _configured_provider_defaults("minmax")
_CLAUDE_CONFIG_DEFAULTS = _configured_provider_defaults("claude")

MINIMAX_API_KEY = _first_non_empty(
    os.getenv("MINIMAX_API_KEY"),
    _MINIMAX_CONFIG_DEFAULTS["api_key"],
)
MINIMAX_BASE_URL = _normalize_openai_base_url(
    _first_non_empty(
        os.getenv("MINIMAX_BASE_URL"),
        _MINIMAX_CONFIG_DEFAULTS["base_url"],
        DEFAULT_MINIMAX_BASE_URL,
    )
)
MINIMAX_MODEL = _first_non_empty(
    os.getenv("MINIMAX_MODEL"),
    _MINIMAX_CONFIG_DEFAULTS["model"],
    DEFAULT_MINIMAX_MODEL,
)

OPENROUTER_API_KEY = _first_non_empty(
    os.getenv("OPENROUTER_API_KEY"),
    os.getenv("ANTHROPIC_API_KEY"),
    os.getenv("CLAUDE_API_KEY"),
    _CLAUDE_CONFIG_DEFAULTS["api_key"],
)
OPENROUTER_BASE_URL = _normalize_openai_base_url(
    _first_non_empty(
        os.getenv("OPENROUTER_BASE_URL"),
        os.getenv("ANTHROPIC_BASE_URL"),
        os.getenv("CLAUDE_BASE_URL"),
        _CLAUDE_CONFIG_DEFAULTS["base_url"],
        DEFAULT_OPENROUTER_BASE_URL,
    )
)
OPENROUTER_MODEL = _first_non_empty(
    os.getenv("OPENROUTER_MODEL"),
    os.getenv("ANTHROPIC_MODEL"),
    os.getenv("CLAUDE_MODEL"),
    _CLAUDE_CONFIG_DEFAULTS["model"],
)

GLM_API_KEY = _first_non_empty(os.getenv("GLM_API_KEY"), MINIMAX_API_KEY)
GLM_BASE_URL = _normalize_openai_base_url(
    _first_non_empty(os.getenv("GLM_BASE_URL"), MINIMAX_BASE_URL)
)

PLANNER_PROVIDER = _first_non_empty(os.getenv("PLANNER_PROVIDER"), "minmax")
_PLANNER_PROVIDER_DEFAULTS = _configured_provider_defaults(PLANNER_PROVIDER)
PLANNER_API_KEY = _first_non_empty(
    os.getenv("PLANNER_API_KEY"),
    _PLANNER_PROVIDER_DEFAULTS["api_key"],
    MINIMAX_API_KEY,
    GLM_API_KEY,
)
PLANNER_BASE_URL = _normalize_openai_base_url(
    _first_non_empty(
        os.getenv("PLANNER_BASE_URL"),
        _PLANNER_PROVIDER_DEFAULTS["base_url"],
        MINIMAX_BASE_URL,
        GLM_BASE_URL,
    )
)
PLANNER_MODEL = _first_non_empty(
    os.getenv("PLANNER_MODEL"),
    _PLANNER_PROVIDER_DEFAULTS["model"],
    MINIMAX_MODEL,
)
MAX_TOKENS_PLANNER = 32768

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_BASE_URL = "https://api.tavily.com"

RESEARCH_PROVIDER = _first_non_empty(os.getenv("RESEARCH_PROVIDER"), PLANNER_PROVIDER)
_RESEARCH_PROVIDER_DEFAULTS = _configured_provider_defaults(RESEARCH_PROVIDER)
RESEARCH_API_KEY = _first_non_empty(
    os.getenv("RESEARCH_API_KEY"),
    _RESEARCH_PROVIDER_DEFAULTS["api_key"],
    PLANNER_API_KEY,
)
RESEARCH_BASE_URL = _normalize_openai_base_url(
    _first_non_empty(
        os.getenv("RESEARCH_BASE_URL"),
        _RESEARCH_PROVIDER_DEFAULTS["base_url"],
        PLANNER_BASE_URL,
    )
)
RESEARCH_MODEL = _first_non_empty(
    os.getenv("RESEARCH_MODEL"),
    _RESEARCH_PROVIDER_DEFAULTS["model"],
    PLANNER_MODEL,
)
MAX_TOKENS_RESEARCHER = 4096


def get_llm_provider_settings(provider: str | None) -> dict[str, str]:
    normalized = (provider or "minmax").strip().lower()
    configured = _configured_provider_defaults(normalized)

    if normalized == "claude":
        return {
            "provider": "claude",
            "api_key": _first_non_empty(
                os.getenv("OPENROUTER_API_KEY"),
                os.getenv("ANTHROPIC_API_KEY"),
                os.getenv("CLAUDE_API_KEY"),
                configured["api_key"],
                OPENROUTER_API_KEY,
            ),
            "base_url": _normalize_openai_base_url(
                _first_non_empty(
                    os.getenv("OPENROUTER_BASE_URL"),
                    os.getenv("ANTHROPIC_BASE_URL"),
                    os.getenv("CLAUDE_BASE_URL"),
                    configured["base_url"],
                    OPENROUTER_BASE_URL,
                )
            ),
            "model": _first_non_empty(
                os.getenv("OPENROUTER_MODEL"),
                os.getenv("ANTHROPIC_MODEL"),
                os.getenv("CLAUDE_MODEL"),
                configured["model"],
                OPENROUTER_MODEL,
            ),
        }

    return {
        "provider": "minmax",
        "api_key": _first_non_empty(
            os.getenv("MINIMAX_API_KEY"),
            configured["api_key"],
            MINIMAX_API_KEY,
            PLANNER_API_KEY,
            GLM_API_KEY,
        ),
        "base_url": _normalize_openai_base_url(
            _first_non_empty(
                os.getenv("MINIMAX_BASE_URL"),
                configured["base_url"],
                MINIMAX_BASE_URL,
                PLANNER_BASE_URL,
                GLM_BASE_URL,
            )
        ),
        "model": _first_non_empty(
            os.getenv("MINIMAX_MODEL"),
            configured["model"],
            MINIMAX_MODEL,
            PLANNER_MODEL,
        ),
    }


# 幻灯片尺寸（英寸，16:9）
SLIDE_WIDTH_INCH = 13.333
SLIDE_HEIGHT_INCH = 7.5

OUTPUT_DIR = "outputs"
ASSETS_DIR = "assets"

# Unsplash
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")
UNSPLASH_BASE_URL = "https://api.unsplash.com"

# 豆包图片生成
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_BASE_URL = _normalize_openai_base_url("https://ark.cn-beijing.volces.com/api/v3")
DOUBAO_IMAGE_MODEL = os.getenv("DOUBAO_IMAGE_MODEL", "doubao-seedream-4-5-251128")
DOUBAO_IMAGE_SIZE = os.getenv("DOUBAO_IMAGE_SIZE", "2K")

# Qwen-VL 视觉评估
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = _normalize_openai_base_url(os.getenv("QWEN_BASE_URL", ""))
QWEN_VL_MODEL = os.getenv("QWEN_VL_MODEL", "qwen-vl-max")
EVAL_SCORE_THRESHOLD = float(os.getenv("EVAL_SCORE_THRESHOLD", "3.0"))
EVAL_MAX_ROUNDS = int(os.getenv("EVAL_MAX_ROUNDS", "2"))
