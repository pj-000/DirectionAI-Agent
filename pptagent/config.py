import os
from dotenv import load_dotenv

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

DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MINIMAX_MODEL = "MiniMax-M2.7"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = _normalize_openai_base_url(os.getenv("MINIMAX_BASE_URL", DEFAULT_MINIMAX_BASE_URL))
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", DEFAULT_MINIMAX_MODEL)

OPENROUTER_API_KEY = os.getenv(
    "OPENROUTER_API_KEY",
    os.getenv("CLAUDE_API_KEY", os.getenv("RESEARCH_API_KEY", "")),
)
OPENROUTER_BASE_URL = _normalize_openai_base_url(
    os.getenv(
        "OPENROUTER_BASE_URL",
        os.getenv("CLAUDE_BASE_URL", os.getenv("RESEARCH_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)),
    )
)
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    os.getenv("CLAUDE_MODEL", os.getenv("RESEARCH_MODEL", "")),
)

GLM_API_KEY = os.getenv("GLM_API_KEY", MINIMAX_API_KEY)
GLM_BASE_URL = _normalize_openai_base_url(os.getenv("GLM_BASE_URL", MINIMAX_BASE_URL))

# Planner Agent (can be configured independently from GLM / Research)
PLANNER_API_KEY = os.getenv("PLANNER_API_KEY", MINIMAX_API_KEY or GLM_API_KEY)
PLANNER_BASE_URL = _normalize_openai_base_url(os.getenv("PLANNER_BASE_URL", GLM_BASE_URL))
PLANNER_MODEL = os.getenv("PLANNER_MODEL", MINIMAX_MODEL)
MAX_TOKENS_PLANNER = 32768

# Research Agent (Tavily)
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_BASE_URL = "https://api.tavily.com"
RESEARCH_API_KEY = os.getenv("RESEARCH_API_KEY", PLANNER_API_KEY)
RESEARCH_BASE_URL = _normalize_openai_base_url(os.getenv("RESEARCH_BASE_URL", PLANNER_BASE_URL))
RESEARCH_MODEL = os.getenv("RESEARCH_MODEL", PLANNER_MODEL)
MAX_TOKENS_RESEARCHER = 4096


def get_llm_provider_settings(provider: str | None) -> dict[str, str]:
    normalized = (provider or "minmax").strip().lower()

    if normalized == "claude":
        return {
            "provider": "claude",
            "api_key": OPENROUTER_API_KEY or RESEARCH_API_KEY or PLANNER_API_KEY,
            "base_url": OPENROUTER_BASE_URL or RESEARCH_BASE_URL or PLANNER_BASE_URL,
            "model": OPENROUTER_MODEL or RESEARCH_MODEL or PLANNER_MODEL,
        }

    return {
        "provider": "minmax",
        "api_key": MINIMAX_API_KEY or PLANNER_API_KEY or GLM_API_KEY,
        "base_url": MINIMAX_BASE_URL or PLANNER_BASE_URL or GLM_BASE_URL,
        "model": MINIMAX_MODEL or PLANNER_MODEL,
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
