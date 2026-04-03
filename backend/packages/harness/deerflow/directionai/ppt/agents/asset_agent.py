"""
agents/asset_agent.py

为 PPT 每页获取配图，支持三种模式：
- image_source="search"   : 仅 Tavily 搜图，搜不到则跳过
- image_source="generate" : 仅豆包生成，不搜索
- image_source="auto"     : Tavily 搜图，搜不到降级豆包生成（默认）
"""
import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional

import httpx
from openai import AsyncOpenAI
from tavily import AsyncTavilyClient

from .. import config
from ..models.schemas import SlideLayout, SlideOutline, VisualMode, resolve_visual_mode

logger = logging.getLogger(__name__)

SKIP_LAYOUTS = {SlideLayout.COVER, SlideLayout.CLOSING, SlideLayout.TOC}


class AssetAgent:
    """
    并发为大纲每页搜索或生成配图，返回本地路径列表。
    列表长度与 slides 一致，无图片的页为 None。
    """

    def __init__(self, image_source: str = "auto"):
        """
        image_source:
          "auto"     — Tavily 搜图，搜不到降级豆包生成
          "search"   — 仅 Tavily 搜图
          "generate" — 仅豆包生成
        """
        self.image_source = image_source
        if image_source in ("auto", "search"):
            self.tavily = AsyncTavilyClient(api_key=config.TAVILY_API_KEY)
        else:
            self.tavily = None
        if image_source in ("auto", "generate"):
            self.ark = AsyncOpenAI(
                api_key=config.ARK_API_KEY,
                base_url=config.ARK_BASE_URL,
            )
        else:
            self.ark = None

    async def fetch_all(
        self,
        slides: list[SlideOutline],
        job_id: str,
        concurrency: int = 3,
    ) -> list[Optional[str]]:
        asset_dir = Path(config.ASSETS_DIR) / job_id
        asset_dir.mkdir(parents=True, exist_ok=True)

        sem = asyncio.Semaphore(concurrency)

        async def _bounded(slide: SlideOutline) -> Optional[str]:
            async with sem:
                return await self._fetch_for_slide(slide, asset_dir)

        results = await asyncio.gather(*[_bounded(s) for s in slides], return_exceptions=True)

        processed = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"[AssetAgent] 第 {i} 页图片获取异常: {r}")
                processed.append(None)
            else:
                processed.append(r)

        fetched = sum(1 for p in processed if p)
        print(f"[AssetAgent] 完成，{fetched}/{len(slides)} 页获取到图片（模式: {self.image_source}）")
        return processed

    async def _fetch_for_slide(self, slide: SlideOutline, asset_dir: Path) -> Optional[str]:
        if slide.layout in SKIP_LAYOUTS:
            return None

        effective_visual_mode = resolve_visual_mode(slide)

        if effective_visual_mode == VisualMode.JS_DIAGRAM:
            print(f"[AssetAgent] 第 {slide.slide_index} 页 visual_mode=js_diagram，跳过图片获取")
            return None

        query = slide.image_prompt.strip() if slide.image_prompt else f"{slide.topic} photo"
        cache_key = hashlib.md5(
            f"{self.image_source}:{effective_visual_mode.value}:{query}".encode()
        ).hexdigest()[:10]

        for ext in (".jpg", ".png"):
            cached = asset_dir / f"{cache_key}{ext}"
            if cached.exists() and cached.stat().st_size > 0:
                print(f"[AssetAgent] 第 {slide.slide_index} 页命中缓存")
                return str(cached)

        if effective_visual_mode == VisualMode.GENERATED_IMAGE:
            if self.image_source in ("auto", "generate"):
                return await self._try_generate(slide, asset_dir, cache_key)
            return await self._try_search(slide, asset_dir, cache_key, query)

        if self.image_source == "generate":
            return await self._try_generate(slide, asset_dir, cache_key)

        if self.image_source == "search":
            return await self._try_search(slide, asset_dir, cache_key, query)

        # auto: 先搜，搜不到再生成
        result = await self._try_search(slide, asset_dir, cache_key, query)
        if result:
            return result
        return await self._try_generate(slide, asset_dir, cache_key)

    async def _try_search(self, slide, asset_dir, cache_key, query) -> Optional[str]:
        image_url = await self._search_image(query)
        if image_url:
            local_path = asset_dir / f"{cache_key}.jpg"
            if await self._download(image_url, local_path):
                print(f"[AssetAgent] 第 {slide.slide_index} 页 Tavily 搜图成功")
                return str(local_path)
        return None

    async def _try_generate(self, slide, asset_dir, cache_key) -> Optional[str]:
        if not self.ark or not config.ARK_API_KEY:
            print(f"[AssetAgent] 第 {slide.slide_index} 页：ARK_API_KEY 未配置，跳过生成")
            return None
        local_path = asset_dir / f"{cache_key}_gen.png"
        prompt = self._make_image_prompt(slide.topic, slide.image_prompt)
        if await self._generate_doubao(prompt, local_path):
            print(f"[AssetAgent] 第 {slide.slide_index} 页豆包生成成功")
            return str(local_path)
        return None

    async def _search_image(self, query: str) -> Optional[str]:
        try:
            result = await self.tavily.search(
                query=query,
                max_results=5,
                search_depth="basic",
                include_images=True,
            )
            images = result.get("images", [])
            for img in images:
                url = img if isinstance(img, str) else img.get("url", "")
                if url and any(url.lower().split("?")[0].endswith(ext)
                               for ext in (".jpg", ".jpeg", ".png", ".webp")):
                    return url
            if images:
                return images[0] if isinstance(images[0], str) else images[0].get("url")
        except Exception as e:
            logger.warning(f"[AssetAgent] Tavily 搜图失败: {e}")
        return None

    async def _generate_doubao(self, prompt: str, output_path: Path) -> bool:
        try:
            response = await self.ark.images.generate(
                model=config.DOUBAO_IMAGE_MODEL,
                prompt=prompt,
                size=config.DOUBAO_IMAGE_SIZE,
                response_format="url",
            )
            url = response.data[0].url
            return await self._download(url, output_path)
        except Exception as e:
            logger.warning(f"[AssetAgent] 豆包生成失败: {e}")
            return False

    async def _download(self, url: str, output_path: Path) -> bool:
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    output_path.write_bytes(r.content)
                    return True
                logger.warning(f"[AssetAgent] 下载失败 HTTP {r.status_code}: {url[:80]}")
        except Exception as e:
            logger.warning(f"[AssetAgent] 下载异常: {e}")
        return False

    @staticmethod
    def _make_image_prompt(topic: str, image_prompt: Optional[str] = None) -> str:
        base = image_prompt.strip() if image_prompt else topic
        return (
            f"{base}，专业演示文稿配图风格，"
            "简洁构图，高对比度，无文字水印，16:9 横向构图，商务专业感，光线明亮"
        )
