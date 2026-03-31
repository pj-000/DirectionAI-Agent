"""DirectionAI Education Tools.

These tools integrate with DirectionAI's specialized education services
(PPT generation, lesson plan generation, exam generation) into the
DeerFlow agent framework.

Usage in config.yaml:
    tools:
      - name: generate_ppt
        group: education
        use: deerflow.directionai.tools:generate_ppt_tool
"""

from .generate_ppt import (
    evaluate_ppt_tool,
    generate_exam_tool,
    generate_lesson_plan_tool,
    generate_ppt_tool,
)

__all__ = [
    "generate_ppt_tool",
    "generate_lesson_plan_tool",
    "generate_exam_tool",
    "evaluate_ppt_tool",
]
