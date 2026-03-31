import time
import os
import asyncio
from typing import Callable
from agents.planner import PlannerAgent
from agents.researcher import ResearchAgent
from agents.asset_agent import AssetAgent
from agents.evaluator import EvaluatorAgent
from tools.pptx_skill import read_pptx, pptx_to_images
import config


class OrchestratorAgent:
    """
    主控 Agent。
    逐页生成 PptxGenJS 代码 → 组装执行 → 视觉 QA 循环 → 产出 .pptx
    """

    def __init__(
        self,
        debug_layout: bool = False,
        no_research: bool = False,
        no_images: bool = False,
        image_source: str = "auto",
        model_provider: str = "minmax",
        thinking_callback: Callable[[str], None] | None = None,
    ):
        self.planner = PlannerAgent(
            model_provider=model_provider,
            thinking_callback=thinking_callback,
        )
        self.researcher = ResearchAgent(
            model_provider=model_provider,
            thinking_callback=thinking_callback,
        )
        self.asset_agent = AssetAgent(image_source=image_source)
        self.evaluator = EvaluatorAgent()
        self.debug_layout = debug_layout
        self.no_research = no_research
        self.no_images = no_images

    def generate(
        self,
        topic: str,
        output_filename: str = "output.pptx",
        language: str = "中文",
        min_slides: int = 6,
        max_slides: int = 10,
        style: str = "",
        audience: str = "general",
        content_requirements: str = "",
    ) -> str:
        print(f"\n{'='*50}")
        print(f"开始生成 PPT：{topic}")
        print(f"{'='*50}\n")

        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        output_path = os.path.abspath(os.path.join(config.OUTPUT_DIR, output_filename))

        try:
            # Step 1: 规划大纲
            outline = self.planner.plan_outline(
                topic,
                min_slides=min_slides,
                max_slides=max_slides,
                style=style,
                audience=audience,
                language=language,
                content_requirements=content_requirements,
            )

            # Step 2: Research → enrich image_prompt → 获取图片
            research_results = None
            image_paths = None

            if not self.no_research and not self.no_images:
                research_results, image_paths = self._research_and_assets(outline, language)
            elif not self.no_research:
                research_results = self._research_outline(outline, language)
            elif not self.no_images:
                image_paths = self._fetch_assets(outline, language)

            # Step 3: 逐页生成 + 组装
            t0 = time.time()
            result_path, slide_codes, theme = self.planner.plan(
                topic,
                output_path=output_path,
                language=language,
                min_slides=min_slides,
                max_slides=max_slides,
                style=style,
                audience=audience,
                content_requirements=content_requirements,
                outline=outline,
                research_results=research_results,
                image_paths=image_paths,
            )
            print(f"[Orchestrator] 逐页生成耗时: {time.time() - t0:.1f}s")

            # Step 4: 文本 QA
            content_issues = self._content_qa(result_path, outline)
            if content_issues:
                print(f"[Orchestrator] 文本 QA 发现 {len(content_issues)} 个问题，修复中...")
                result_path, slide_codes, theme = self._fix_content_issues(
                    content_issues, slide_codes, theme, outline, research_results, image_paths, output_path
                )

            # Step 5: 视觉 QA 循环
            result_path = self._qa_loop(
                result_path, slide_codes, theme, outline, research_results, image_paths
            )

            if self.debug_layout:
                self._print_debug(result_path)

            print(f"\n{'='*50}")
            print(f"生成完成！文件路径：{result_path}")
            print(f"{'='*50}\n")

            return result_path

        except Exception as e:
            print(f"\n[Orchestrator] 生成失败: {e}")
            raise

    def _qa_loop(
        self,
        output_path: str,
        slide_codes: list[str],
        theme: dict,
        outline,
        research_results,
        image_paths,
    ) -> str:
        """
        视觉 QA 循环：转图片 → 评分 → 修复低分页 → 重新组装。
        按照 SKILL.md 要求，至少完成一轮完整的检查-修复-再验证循环。
        """
        if not self.evaluator.enabled:
            return output_path

        did_fix = False

        for round_i in range(1, config.EVAL_MAX_ROUNDS + 1):
            print(f"\n[Orchestrator] QA 第 {round_i} 轮：转换幻灯片为图片...")
            images = pptx_to_images(output_path)
            if not images:
                print("[Orchestrator] 图片转换失败，跳过 QA")
                break

            eval_results = self.evaluator.evaluate_all(images, outline)
            if not eval_results:
                break

            low_score = [r for r in eval_results if r.overall < config.EVAL_SCORE_THRESHOLD]

            if not low_score:
                if did_fix:
                    print(f"[Orchestrator] QA 通过，修复后所有页评分达标")
                else:
                    print(f"[Orchestrator] QA 首轮无低分页，所有页评分达标")
                break

            print(f"[Orchestrator] 第 {round_i} 轮修复 {len(low_score)} 页低分页...")
            did_fix = True
            research_results = research_results or []
            image_paths = image_paths or []

            for result in low_score:
                idx = result.slide_index
                if idx >= len(outline.slides):
                    continue
                slide = outline.slides[idx]
                research = research_results[idx] if idx < len(research_results) else None
                img = image_paths[idx] if idx < len(image_paths) else None
                prev_summary = "\n".join(
                    f"第{s.slide_index}页 [{s.layout.value}] {s.topic}"
                    for s in outline.slides[:idx]
                )
                print(f"[Orchestrator] 重新生成第 {idx} 页（overall={result.overall:.1f}）...")
                new_code = self.planner.plan_slide(
                    slide, theme, research, img,
                    prev_slides_summary=prev_summary,
                    revision_feedback=result,
                )
                if idx < len(slide_codes):
                    slide_codes[idx] = new_code

            output_path = self.planner.assemble_pptx(slide_codes, output_path, theme)

        return output_path

    def _content_qa(self, pptx_path: str, outline) -> list[dict]:
        """
        文本 QA：用 markitdown 提取文本，检查每页内容完整性。
        返回有问题的页列表：[{"slide_index": i, "issues": [...]}]
        """
        print("[Orchestrator] 文本 QA：提取 PPTX 文本内容...")
        content = read_pptx(pptx_path)
        if not content:
            print("[Orchestrator] markitdown 不可用，跳过文本 QA")
            return []

        # 按 slide 分割
        slides_text = content.split("<!-- Slide number:")
        slides_text = [s.strip() for s in slides_text if s.strip()]

        issues_list = []
        for slide in outline.slides:
            idx = slide.slide_index
            issues = []

            # 找到对应页的文本
            slide_content = ""
            for st in slides_text:
                if st.startswith(f" {idx + 1} ") or st.startswith(f"{idx + 1} "):
                    slide_content = st
                    break

            if not slide_content and slide.layout.value not in ("cover", "closing"):
                issues.append("页面内容为空")
            elif slide_content:
                # 检查占位符残留
                import re
                placeholders = re.findall(
                    r"(?:xxxx|lorem|ipsum|placeholder|sample text|click to add)",
                    slide_content, re.IGNORECASE
                )
                if placeholders:
                    issues.append(f"发现占位符残留：{placeholders}")

                # 检查内容页是否太短
                text_len = len(slide_content.strip())
                if slide.layout.value in ("content", "two_column") and text_len < 80:
                    issues.append(f"内容页文字过少（{text_len} 字符），可能缺失要点")

                # 检查标题是否存在
                if slide.topic and slide.topic not in slide_content and slide.layout.value not in ("cover", "closing", "toc"):
                    # 宽松检查：标题的前几个字是否出现
                    topic_prefix = slide.topic[:6]
                    if topic_prefix not in slide_content:
                        issues.append(f"页面标题可能缺失：{slide.topic}")

            if issues:
                print(f"[ContentQA] 第 {idx} 页问题：{'; '.join(issues)}")
                issues_list.append({"slide_index": idx, "issues": issues})

        if not issues_list:
            print("[Orchestrator] 文本 QA 通过，无内容问题")

        return issues_list

    def _fix_content_issues(
        self, content_issues, slide_codes, theme, outline, research_results, image_paths, output_path
    ) -> tuple[str, list[str], dict]:
        """针对文本 QA 发现的问题页重新生成。"""
        research_results = research_results or []
        image_paths = image_paths or []

        for issue in content_issues:
            idx = issue["slide_index"]
            if idx >= len(outline.slides) or idx >= len(slide_codes):
                continue
            slide = outline.slides[idx]
            research = research_results[idx] if idx < len(research_results) else None
            img = image_paths[idx] if idx < len(image_paths) else None
            prev_summary = "\n".join(
                f"第{s.slide_index}页 [{s.layout.value}] {s.topic}"
                for s in outline.slides[:idx]
            )

            # 构造一个简单的 feedback 对象传递文本问题
            from models.schemas import SlideEvalResult
            feedback = SlideEvalResult(
                slide_index=idx,
                layout_score=3.0,
                content_score=1.0,
                design_score=3.0,
                overall=2.0,
                issues=issue["issues"],
                suggestions=["确保页面标题存在", "确保内容要点完整呈现", "不要留下占位符文字"],
            )

            print(f"[Orchestrator] 重新生成第 {idx} 页（文本问题）...")
            new_code = self.planner.plan_slide(
                slide, theme, research, img,
                prev_slides_summary=prev_summary,
                revision_feedback=feedback,
            )
            slide_codes[idx] = new_code

        result_path = self.planner.assemble_pptx(slide_codes, output_path, theme)
        return result_path, slide_codes, theme

    def _research_and_assets(self, outline, language: str):
        """Research 先执行，完成后用结果丰富 image_prompt，再获取图片。"""
        import uuid
        job_id = str(uuid.uuid4())[:8]
        slides = self.planner.outline_to_research_slides(outline)

        print("[Orchestrator] ResearchAgent 逐页研究中...")
        try:
            research_results = asyncio.run(self.researcher.research_all(slides, language=language))
        except Exception as e:
            print(f"[Orchestrator] ResearchAgent 失败: {e}")
            research_results = []

        researched = sum(1 for r in research_results if r and r.get("bullet_points"))
        print(f"[Orchestrator] ResearchAgent 完成，{researched} 页拿到研究要点")

        outline = self.planner.enrich_image_prompts(outline, research_results)

        print("[Orchestrator] AssetAgent 获取图片中...")
        try:
            image_paths = asyncio.run(self.asset_agent.fetch_all(outline.slides, job_id=job_id))
        except Exception as e:
            print(f"[Orchestrator] AssetAgent 失败: {e}")
            image_paths = []

        fetched = sum(1 for p in image_paths if p)
        print(f"[Orchestrator] AssetAgent 完成，{fetched} 页获取到图片")

        return research_results, image_paths

    def _research_outline(self, outline, language: str) -> list[dict | None]:
        """仅 research，不获取图片。"""
        print("[Orchestrator] ResearchAgent 逐页研究中...")
        try:
            slides = self.planner.outline_to_research_slides(outline)
            results = asyncio.run(self.researcher.research_all(slides, language=language))
        except Exception as e:
            print(f"[Orchestrator] ResearchAgent 跳过: {e}")
            return []
        researched_pages = sum(1 for r in results if r and r.get("bullet_points"))
        print(f"[Orchestrator] ResearchAgent 完成，{researched_pages} 页拿到研究要点")
        return results

    def _fetch_assets(self, outline, language: str) -> list:
        """仅获取图片，不 research。"""
        import uuid
        job_id = str(uuid.uuid4())[:8]
        print("[Orchestrator] AssetAgent 获取图片中...")
        try:
            paths = asyncio.run(self.asset_agent.fetch_all(outline.slides, job_id=job_id))
        except Exception as e:
            print(f"[Orchestrator] AssetAgent 跳过: {e}")
            return []
        return paths

    def _print_debug(self, pptx_path: str):
        """用 markitdown 提取内容做调试输出"""
        content = read_pptx(pptx_path)
        if content:
            print(f"\n{'─'*50}")
            print("[DEBUG] 提取的文本内容：")
            print(content[:2000])
            print(f"{'─'*50}\n")
        else:
            print("[DEBUG] markitdown 不可用，跳过内容提取")
