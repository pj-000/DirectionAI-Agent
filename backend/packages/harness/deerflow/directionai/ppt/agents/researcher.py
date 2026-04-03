import json
import re
import asyncio
import logging
from pathlib import Path
from typing import Callable
from openai import AsyncOpenAI
from tavily import AsyncTavilyClient
from ..models.schemas import SlideSpec, SlideLayout, TextElement
from ..tools.openai_compat import async_stream_chat_completion_text, build_chat_completion_kwargs
from ..tools.pptx_skill import PROJECT_ROOT
from .. import config

logger = logging.getLogger(__name__)


class ResearchAgent:
    """用 Tavily 搜索每页主题，再用 LLM 提炼为 PPT 要点。"""

    SKIP_LAYOUTS = {SlideLayout.COVER, SlideLayout.CLOSING, SlideLayout.TOC}

    def __init__(
        self,
        model_provider: str = "minmax",
        thinking_callback: Callable[[str], None] | None = None,
    ):
        if not config.TAVILY_API_KEY or not config.TAVILY_API_KEY.strip():
            self.tavily = None
            self.llm = None
            self.client = None
            self._system_template = ""
            self.thinking_callback = thinking_callback
            print("[Research] TAVILY_API_KEY 未配置，Research 功能已禁用")
            return

        provider_settings = config.get_llm_provider_settings(model_provider)
        self.model = provider_settings["model"]
        self.tavily = AsyncTavilyClient(api_key=config.TAVILY_API_KEY)
        self.llm = AsyncOpenAI(
            api_key=provider_settings["api_key"] or config.RESEARCH_API_KEY,
            base_url=provider_settings["base_url"] or config.RESEARCH_BASE_URL,
        )
        self.client = self.llm
        self.last_reasoning = ""
        self.thinking_callback = thinking_callback
        self._system_template = (PROJECT_ROOT / "prompts" / "researcher_system.txt").read_text(encoding="utf-8")

    def _handle_reasoning_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self.last_reasoning += chunk
        if self.thinking_callback:
            self.thinking_callback(chunk)

    async def research_topic(self, topic: str, language: str = "中文") -> dict:
        """
        对整份 PPT 主题做一次前置研究，供 Planner 生成时参考。
        当前主流程还没有“先产出 slides 再逐页 research”的中间态，
        所以这里用一个 synthetic content slide 复用现有 research_slide 逻辑。
        """
        slide = SlideSpec(
            slide_index=0,
            layout=SlideLayout.CONTENT,
            topic=topic,
            elements=[
                TextElement(
                    type="title",
                    content=topic,
                    x=0.5,
                    y=0.3,
                    width=12.0,
                    height=0.9,
                    font_size=32,
                    bold=True,
                    color="#1F3864",
                )
            ],
        )
        result = await self.research_slide(slide, language=language)
        return result or {
            "topic": topic,
            "summary": topic,
            "bullet_points": [],
        }

    async def research_slide(self, slide: SlideSpec, language: str = "中文") -> dict | None:
        """
        对单页做 Tavily 搜索 + GLM 提炼。
        cover / closing / toc 直接返回 None。
        失败时返回默认结构，不让全流程崩溃。
        """
        if slide.layout in self.SKIP_LAYOUTS:
            return None

        try:
            # Step 1: Tavily 搜索
            search_result = await self.tavily.search(
                query=slide.topic,
                max_results=3,
                search_depth="basic",
            )
            snippets = [r.get("content", "") for r in search_result.get("results", [])]
            context = "\n\n".join(snippets[:3])

            # Step 2: LLM 提炼为 PPT 要点
            system_prompt = self._system_template.replace("{language}", language)
            user_prompt = (
                f"页面主题：{slide.topic}\n\n"
                f"以下是搜索到的参考资料：\n{context[:2000]}\n\n"
                f"请根据以上资料，为该 PPT 页面生成精炼的内容。"
            )

            self.last_reasoning = ""
            raw, reasoning_text = await async_stream_chat_completion_text(
                self.client,
                model=self.model,
                max_tokens=config.MAX_TOKENS_RESEARCHER,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                on_reasoning_chunk=self._handle_reasoning_chunk,
                **build_chat_completion_kwargs(self.model),
            )
            self.last_reasoning = reasoning_text
            # 调试：保存完整响应
            if slide.slide_index in [3, 9]:  # 只保存失败的页面
                debug_path = f"debug_research_page_{slide.slide_index}.txt"
                Path(debug_path).write_text(raw, encoding="utf-8")
                print(f"[Research] 第 {slide.slide_index} 页完整响应已保存到 {debug_path}")
            data = self._parse_json(raw)

            if "bullet_points" not in data or not isinstance(data["bullet_points"], list):
                raise ValueError("缺少 bullet_points 字段")

            print(f"[Research] 第 {slide.slide_index} 页完成: {slide.topic}")
            return data

        except Exception as e:
            logger.warning(f"[Research] 第 {slide.slide_index} 页失败: {e}")
            print(f"[Research] 第 {slide.slide_index} 页失败，使用默认内容: {e}")
            return {
                "topic": slide.topic,
                "summary": slide.topic,
                "bullet_points": [],
            }

    async def research_all(
        self, slides: list[SlideSpec], language: str = "中文", concurrency: int = 3
    ) -> list[dict | None]:
        """并发研究所有页面，返回列表长度与 slides 一致。"""
        sem = asyncio.Semaphore(concurrency)

        async def _bounded(slide: SlideSpec):
            async with sem:
                return await self.research_slide(slide, language)

        results = await asyncio.gather(*[_bounded(s) for s in slides])
        return list(results)

    def _parse_json(self, raw: str) -> dict:
        cleaned = raw.strip()
        # 去掉 ```json ... ``` 围栏
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        # 智能替换 JSON 字符串值内的中文引号
        def fix_quotes(text: str) -> str:
            result = []
            in_string = False
            i = 0
            while i < len(text):
                ch = text[i]

                # 处理转义
                if ch == '\\' and i + 1 < len(text):
                    result.append(ch)
                    result.append(text[i + 1])
                    i += 2
                    continue

                # 英文双引号：切换字符串状态
                if ch == '"':
                    in_string = not in_string
                    result.append(ch)
                # 在字符串内：替换中文引号和单引号
                elif in_string:
                    if ch in '""':
                        result.append('\\"')
                    elif ch in '\u2018\u2019':  # 中文单引号
                        result.append("'")
                    else:
                        result.append(ch)
                else:
                    result.append(ch)

                i += 1
            return ''.join(result)

        cleaned = fix_quotes(cleaned)

        # 尝试直接解析
        try:
            result = json.loads(cleaned, strict=False)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError as e:
            logger.debug(f"直接解析失败: {e}")

        # fallback：找最外层的 { 到匹配的 }
        first_brace = cleaned.find("{")
        if first_brace >= 0:
            depth = 0
            in_string = False
            escape = False

            for i in range(first_brace, len(cleaned)):
                char = cleaned[i]

                if escape:
                    escape = False
                    continue

                if char == '\\':
                    escape = True
                    continue

                if char == '"' and not escape:
                    in_string = not in_string
                    continue

                if not in_string:
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0:
                            candidate = cleaned[first_brace:i + 1]
                            try:
                                result = json.loads(candidate, strict=False)
                                if isinstance(result, dict):
                                    return result
                            except json.JSONDecodeError:
                                pass
                            break

        raise ValueError(f"无法从响应中提取 JSON dict，原始内容前200字：{cleaned[:200]}")
