import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main


def test_build_parser_registers_slides_style_and_audience():
    parser = main.build_parser()
    args = parser.parse_args([
        "--topic", "人工智能",
        "--slides", "8",
        "--style", "minimal",
        "--audience", "boss",
        "--no-research",
    ])

    assert args.topic == "人工智能"
    assert args.slides == "8"
    assert args.style == "minimal"
    assert args.audience == "boss"
    assert args.research is False


def test_parse_slide_range_for_fixed_count():
    assert main.parse_slide_range("8") == (8, 8)


def test_parse_slide_range_for_range():
    assert main.parse_slide_range("10-6") == (6, 10)


def test_parse_slide_range_invalid_uses_default():
    assert main.parse_slide_range("abc") == (6, 10)


def test_ask_yes_no_accepts_yes_variants(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "是")
    assert main.ask_yes_no("test", default=False) is True
