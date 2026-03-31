import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
from agents.orchestrator import OrchestratorAgent
from models.schemas import OutlinePlan


def _make_outline():
    return OutlinePlan.model_validate(
        {
            "title": "人工智能",
            "topic": "人工智能",
            "slides": [
                {"slide_index": 0, "layout": "cover", "topic": "封面"},
                {"slide_index": 1, "layout": "toc", "topic": "目录"},
                {"slide_index": 2, "layout": "content", "topic": "核心技术"},
                {"slide_index": 3, "layout": "closing", "topic": "总结"},
            ],
        }
    )


def test_generate_calls_outline_then_research_then_render(tmp_path):
    with patch("agents.orchestrator.PlannerAgent") as mock_planner_cls:
        with patch("agents.orchestrator.ResearchAgent") as mock_researcher_cls:
            planner = MagicMock()
            planner.plan_outline.return_value = _make_outline()
            planner.plan.return_value = (str(tmp_path / "demo.pptx"), [], {})
            mock_planner_cls.return_value = planner

            researcher = MagicMock()
            mock_researcher_cls.return_value = researcher

            agent = OrchestratorAgent(no_images=True)
            with patch.object(agent, "_research_outline", return_value=[None, None, {"summary": "测试", "bullet_points": ["要点1"]}, None]):
                result = agent.generate("人工智能", output_filename="demo.pptx")

    assert result.endswith("demo.pptx")
    planner.plan_outline.assert_called_once()
    planner.plan.assert_called_once()
    assert planner.plan.call_args.kwargs["outline"].topic == "人工智能"
    assert planner.plan.call_args.kwargs["research_results"][2]["bullet_points"] == ["要点1"]


def test_generate_skips_research_when_disabled(tmp_path):
    with patch("agents.orchestrator.PlannerAgent") as mock_planner_cls:
        with patch("agents.orchestrator.ResearchAgent"):
            planner = MagicMock()
            planner.plan_outline.return_value = _make_outline()
            planner.plan.return_value = (str(tmp_path / "demo.pptx"), [], {})
            mock_planner_cls.return_value = planner

            agent = OrchestratorAgent(no_research=True, no_images=True)
            with patch.object(agent, "_research_outline") as mock_research:
                agent.generate("人工智能", output_filename="demo.pptx")

    mock_research.assert_not_called()
    assert planner.plan.call_args.kwargs["research_results"] is None
