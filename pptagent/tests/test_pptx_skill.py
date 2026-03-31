import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.pptx_skill import (
    _default_preview_dir,
    _build_soffice_convert_commands,
    _collect_slide_images,
    _cleanup_preview_images,
    _extract_syntax_error_location,
    _find_libreoffice_app_soffice,
    _repair_common_js_syntax_errors,
    _slide_image_sort_key,
    get_preview_runtime_diagnostics,
)
from unittest.mock import patch


def test_repair_common_js_syntax_errors_fixes_escaped_closing_quote_before_property():
    bad = (
        '{ text: "公元前 3 世纪阿基米德利用\\"穷竭法\\"计算曲线面积，孕育积分雏形。\\", '
        'options: { bullet: true, breakLine: true } },'
    )
    repaired = _repair_common_js_syntax_errors(bad, "SyntaxError: Invalid or unexpected token")

    assert '孕育积分雏形。", options:' in repaired
    assert '利用\\"穷竭法\\"' in repaired


def test_repair_common_js_syntax_errors_keeps_valid_code_unchanged_without_syntax_error():
    good = '{ text: "He said \\"hello\\", then left.", options: { bullet: true } },'
    repaired = _repair_common_js_syntax_errors(good, "ReferenceError: x is not defined")

    assert repaired == good


def test_extract_syntax_error_location_reads_node_caret_output():
    stderr = (
        "/tmp/example.js:2\n"
        '  { text: "用\\u0022任意小的 ε"约束输出精度", options: { color: "3A3F5C" } }\n'
        "                           ^^^^^^\n"
        "SyntaxError: Unexpected identifier '约束输出精度'\n"
    )

    assert _extract_syntax_error_location(stderr) == (2, 27)


def test_repair_common_js_syntax_errors_uses_caret_to_escape_mixed_script_quote():
    bad = (
        "const items = [\n"
        '  { text: "用\\u0022任意小的 ε"约束输出精度，用\\u0022足够小的 δ"控制输入范围", '
        'options: { color: "3A3F5C" } },\n'
        "];\n"
    )
    stderr = (
        "/tmp/example.js:2\n"
        '  { text: "用\\u0022任意小的 ε"约束输出精度，用\\u0022足够小的 δ"控制输入范围", '
        'options: { color: "3A3F5C" } },\n'
        "                           ^^^^^^\n"
        "SyntaxError: Unexpected identifier '约束输出精度'\n"
    )

    repaired = _repair_common_js_syntax_errors(bad, stderr)

    assert 'ε\\u0022约束输出精度' in repaired


def test_slide_image_sort_key_orders_numeric_suffixes():
    paths = [
        "/tmp/slide-10.jpg",
        "/tmp/slide-2.jpg",
        "/tmp/slide-01.jpg",
    ]

    ordered = sorted(paths, key=_slide_image_sort_key)

    assert ordered == [
        "/tmp/slide-01.jpg",
        "/tmp/slide-2.jpg",
        "/tmp/slide-10.jpg",
    ]


def test_cleanup_preview_images_removes_stale_slide_files(tmp_path):
    slide_path = tmp_path / "slide-1.jpg"
    pdf_path = tmp_path / "temp.pdf"
    keep_path = tmp_path / "notes.txt"
    slide_path.write_bytes(b"jpg")
    pdf_path.write_bytes(b"pdf")
    keep_path.write_text("keep", encoding="utf-8")

    _cleanup_preview_images(str(tmp_path))

    assert not slide_path.exists()
    assert not pdf_path.exists()
    assert keep_path.exists()


def test_collect_slide_images_sorts_and_filters_preview_files(tmp_path):
    for name in ["slide-10.jpg", "slide-2.jpg", "slide-01.jpg", "notes.txt"]:
        path = tmp_path / name
        if name.endswith(".txt"):
            path.write_text("x", encoding="utf-8")
        else:
            path.write_bytes(b"img")

    images = _collect_slide_images(str(tmp_path))

    assert [os.path.basename(p) for p in images] == ["slide-01.jpg", "slide-2.jpg", "slide-10.jpg"]


def test_default_preview_dir_uses_pptx_basename(tmp_path):
    pptx_path = tmp_path / "demo deck.pptx"

    preview_dir = _default_preview_dir(str(pptx_path))

    assert preview_dir == str(tmp_path / "slides_preview" / "demo deck")


def test_find_libreoffice_app_soffice_prefers_app_bundle_on_macos():
    with patch("tools.pptx_skill.os.path.isfile", side_effect=lambda p: p == "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        with patch("tools.pptx_skill.os.access", return_value=True):
            assert _find_libreoffice_app_soffice() == "/Applications/LibreOffice.app/Contents/MacOS/soffice"


def test_build_soffice_convert_commands_prefers_open_then_binary_on_macos():
    with patch("tools.pptx_skill.sys.platform", "darwin"):
        with patch("tools.pptx_skill._find_libreoffice_app_soffice", return_value="/Applications/LibreOffice.app/Contents/MacOS/soffice"):
            with patch("tools.pptx_skill._find_binary", return_value="/opt/homebrew/bin/soffice"):
                commands = _build_soffice_convert_commands("/tmp/in.pptx", "/tmp/out")

    assert commands[0][:6] == ["open", "-g", "-W", "-n", "-a", "LibreOffice"]
    assert commands[1][0] == "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    assert commands[2][0] == "/opt/homebrew/bin/soffice"


def test_get_preview_runtime_diagnostics_reports_missing_binaries():
    with patch("tools.pptx_skill.sys.platform", "linux"):
        with patch("tools.pptx_skill._find_binary", return_value=None):
            info = get_preview_runtime_diagnostics()

    assert info["platform"] == "linux"
    assert info["soffice_found"] is False
    assert info["pdftoppm_found"] is False
