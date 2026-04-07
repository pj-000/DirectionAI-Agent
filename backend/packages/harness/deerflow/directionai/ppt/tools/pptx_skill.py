"""
tools/pptx_skill.py

本地封装 Anthropic 官方 PPTX skill（vendor/anthropic_pptx_skill）。

能力：
1. run_js(code, output_path) — 执行 PptxGenJS 代码生成 .pptx
2. read_pptx(path) — 用 vendored SKILL 推荐的 markitdown 提取文本
3. pptx_to_images(path) — 调用 vendored scripts/office/soffice.py + pdftoppm 转图
4. skill_paths() — 返回本地 skill 路径，便于 planner 读取本地文档
"""
import os
import re
import sys
import subprocess
import tempfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = PROJECT_ROOT / "vendor" / "anthropic_pptx_skill"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
OFFICE_ROOT = SCRIPTS_ROOT / "office"

# 全局 node_modules 路径
_NPM_PREFIX = subprocess.run(
    ["npm", "config", "get", "prefix"],
    capture_output=True, text=True, timeout=10
).stdout.strip()
NODE_PATH = os.path.join(_NPM_PREFIX, "lib", "node_modules")


def skill_paths() -> dict:
    """返回本地 vendored skill 的关键路径。"""
    return {
        "root": str(SKILL_ROOT),
        "skill_md": str(SKILL_ROOT / "SKILL.md"),
        "pptxgenjs_md": str(SKILL_ROOT / "pptxgenjs.md"),
        "local_rules_md": str(SKILL_ROOT / "local_rules.md"),
        "thumbnail_py": str(SCRIPTS_ROOT / "thumbnail.py"),
        "soffice_py": str(OFFICE_ROOT / "soffice.py"),
        "unpack_py": str(OFFICE_ROOT / "unpack.py"),
        "pack_py": str(OFFICE_ROOT / "pack.py"),
        "validate_py": str(OFFICE_ROOT / "validate.py"),
    }


def assert_skill_present():
    """确保本地 vendored skill 存在。"""
    required = [
        SKILL_ROOT / "SKILL.md",
        SKILL_ROOT / "pptxgenjs.md",
        SCRIPTS_ROOT / "thumbnail.py",
        OFFICE_ROOT / "soffice.py",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "本地 Anthropic PPTX skill 不完整，缺少: " + ", ".join(missing)
        )


def _repair_common_js_syntax_errors(code: str, stderr: str) -> str:
    """
    修复 LLM 生成代码里常见的字符串闭合错误。

    优先根据 Node 报错里标出的 caret 位置，修复整块“逐行字符串化”的伪代码；
    如果不是这种情况，再定点转义出错位置左侧最近的裸引号；
    如果拿不到位置信息，再回退到少量窄范围正则修复。
    """
    if "SyntaxError" not in stderr:
        return code

    repaired = _repair_quoted_js_block_near_syntax_error(code, stderr)
    if repaired != code:
        return repaired

    repaired = _repair_quote_near_syntax_error(code, stderr)
    if repaired != code:
        return repaired

    repaired = _repair_missing_object_property_comma(code, stderr)
    if repaired != code:
        return repaired

    repaired = code
    patterns = [
        # 例如：text: "内容\", options: ...
        (r'\\(["\'])\s*,\s*([A-Za-z_$][\w$]*\s*:)', r'\1, \2'),
        # 例如：const x = "内容\");
        (r'\\(["\'])\s*([}\]\),;])', r'\1\2'),
    ]

    for pattern, replacement in patterns:
        repaired = re.sub(pattern, replacement, repaired)

    return repaired


def _repair_quoted_js_block_near_syntax_error(code: str, stderr: str) -> str:
    """
    修复这类被错误生成为“逐行字符串列表”的代码块：

    {
      "let slide = pres.addSlide();",
      "slide.addShape(\u0022rect", {",
      \u0022  x: 0, y: 0, w: 1, h: 1,",
      "});"
    }

    参考现有 caret-based 修复思路：只围绕报错位置附近做定点修复，
    避免误改正常代码。
    """
    location = _extract_syntax_error_location(stderr)
    if not location:
        return code

    line_no, _caret_col = location
    lines = code.splitlines(keepends=True)
    if line_no < 1 or line_no > len(lines):
        return code

    error_index = line_no - 1
    start = None
    for idx in range(error_index, -1, -1):
        if lines[idx].strip() == "{":
            start = idx
            break
    if start is None:
        return code

    end = None
    for idx in range(error_index, len(lines)):
        if lines[idx].strip() == "}":
            end = idx
            break
    if end is None or end <= start + 1:
        return code

    decoded_lines: list[str] = []
    candidate_count = 0
    js_like_count = 0

    for idx in range(start + 1, end):
        decoded = _decode_quoted_js_line(lines[idx])
        if decoded is None:
            return code
        decoded_lines.append(decoded)
        if decoded.strip():
            candidate_count += 1
            if any(
                marker in decoded
                for marker in (
                    "slide.",
                    "pres.",
                    "let ",
                    "const ",
                    "function ",
                    "=>",
                    "addText(",
                    "addShape(",
                    "addImage(",
                    "writeFile(",
                    "background =",
                    "//",
                )
            ):
                js_like_count += 1

    if candidate_count < 3 or js_like_count < 2:
        return code

    rebuilt = lines[:start] + ["{\n"] + [line + "\n" for line in decoded_lines] + ["}\n"] + lines[end + 1:]
    return "".join(rebuilt)


def _decode_quoted_js_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return ""

    if stripped.startswith('"') and (stripped.endswith('",') or stripped.endswith('"')):
        end = -2 if stripped.endswith('",') else -1
        decoded = stripped[1:end]
    elif stripped.startswith("\\u0022") and (stripped.endswith('",') or stripped.endswith('"')):
        end = -2 if stripped.endswith('",') else -1
        decoded = stripped[len("\\u0022"):end]
    else:
        return None

    decoded = decoded.replace('\\"', '"')
    decoded = decoded.replace("\\u0022", '"')
    decoded = decoded.replace("\\u0027", "'")
    return decoded


def _repair_quote_near_syntax_error(code: str, stderr: str) -> str:
    """根据 Node SyntaxError 的 caret 位置，修复该位置左侧最近的裸引号。"""
    location = _extract_syntax_error_location(stderr)
    if not location:
        return code

    line_no, caret_col = location
    lines = code.splitlines(keepends=True)
    if line_no < 1 or line_no > len(lines):
        return code

    line = lines[line_no - 1]
    line_body = line.rstrip("\r\n")
    if not line_body:
        return code

    search_start = min(max(caret_col - 1, 0), len(line_body) - 1)
    for i in range(search_start, -1, -1):
        ch = line_body[i]
        if ch not in ('"', "'"):
            continue
        if i > 0 and line_body[i - 1] == "\\":
            continue

        replacement = "\\u0022" if ch == '"' else "\\u0027"
        suffix = line[len(line_body):]
        lines[line_no - 1] = line_body[:i] + replacement + line_body[i + 1:] + suffix
        return "".join(lines)

    return code


def _repair_missing_object_property_comma(code: str, stderr: str) -> str:
    """
    修复多行对象字面量中遗漏逗号导致的：

      fill: { color: "FFFFFF" }
      shadow: { ... }

    这类 `Unexpected identifier 'shadow'` 错误。
    """
    location = _extract_syntax_error_location(stderr)
    if not location:
        return code

    line_no, _caret_col = location
    lines = code.splitlines(keepends=True)
    if line_no < 1 or line_no > len(lines):
        return code

    current_index = line_no - 1
    current_line = lines[current_index]
    current_body = current_line.rstrip("\r\n")

    property_match = re.match(r"^(\s*)([A-Za-z_$][\w$]*)\s*:", current_body)
    if not property_match:
        return code

    prev_index = _find_previous_nonempty_line(lines, current_index)
    if prev_index is None:
        return code

    prev_body = lines[prev_index].rstrip("\r\n")
    stripped_prev = prev_body.strip()
    if not stripped_prev:
        return code

    if stripped_prev.endswith((",", "{", "[", "(", ":")):
        return code

    # 常见合法结束，直接补逗号即可。
    if re.search(r'[\]})"\']\s*$', stripped_prev) or re.search(r"\b(?:true|false|null|\d+(?:\.\d+)?)\s*$", stripped_prev):
        suffix = lines[prev_index][len(prev_body):]
        lines[prev_index] = prev_body + "," + suffix
        return "".join(lines)

    return code


def _find_previous_nonempty_line(lines: list[str], start_index: int) -> int | None:
    """返回 start_index 之前最近的非空行索引。"""
    for idx in range(start_index - 1, -1, -1):
        if lines[idx].strip():
            return idx
    return None


def _extract_syntax_error_location(stderr: str) -> tuple[int, int] | None:
    """
    从 Node SyntaxError 输出中提取 (line_no, caret_col)。
    caret_col 为 0-based 列号。
    """
    match = re.search(r":(\d+)\n[^\n]*\n([ \t]*)\^", stderr)
    if not match:
        return None
    return int(match.group(1)), len(match.group(2))


def _cleanup_preview_images(output_dir: str) -> None:
    """清理旧的 slide 预览图和临时 PDF，避免多轮 QA 混入历史文件。"""
    if not os.path.isdir(output_dir):
        return

    for name in os.listdir(output_dir):
        lowered = name.lower()
        if (
            name.startswith("slide") and lowered.endswith((".jpg", ".jpeg", ".png"))
        ) or lowered.endswith(".pdf"):
            try:
                os.unlink(os.path.join(output_dir, name))
            except FileNotFoundError:
                pass


def _slide_image_sort_key(path: str) -> tuple[int, str]:
    """按页码数字排序 slide-1.jpg / slide-01.jpg / slide-10.jpg。"""
    name = os.path.basename(path)
    match = re.search(r"(\d+)(?=\.[^.]+$)", name)
    if not match:
        return (10**9, name)
    return (int(match.group(1)), name)


def _collect_slide_images(output_dir: str) -> list[str]:
    """收集并按页码排序当前目录里的 slide 预览图。"""
    images = [
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.startswith("slide") and f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]
    return sorted(images, key=_slide_image_sort_key)


def _default_preview_dir(pptx_path: str) -> str:
    base = os.path.splitext(os.path.basename(pptx_path))[0]
    return os.path.join(os.path.dirname(pptx_path), "slides_preview", base)


def run_js(code: str, output_path: str, timeout: int = 60) -> str:
    """
    执行一段 PptxGenJS JavaScript 代码，生成 .pptx 文件。
    使用本地 node + pptxgenjs。
    """
    assert_skill_present()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_js = f.name

    try:
        env = os.environ.copy()
        env["NODE_PATH"] = NODE_PATH

        code_to_run = code
        last_result = None

        max_attempts = 5
        for attempt in range(max_attempts):
            if attempt > 0:
                Path(tmp_js).write_text(code_to_run, encoding="utf-8")

            result = subprocess.run(
                ["node", tmp_js],
                capture_output=True, text=True,
                timeout=timeout, env=env,
            )
            last_result = result

            if result.returncode == 0:
                break

            repaired = _repair_common_js_syntax_errors(code_to_run, result.stderr)
            if repaired == code_to_run:
                break

            logger.warning(
                "[PptxSkill] 检测到可修复的 JS SyntaxError，自动修复后重试（第 %s/%s 次）",
                attempt + 2,
                max_attempts,
            )
            code_to_run = repaired

        result = last_result

        if result.returncode != 0:
            raise RuntimeError(
                f"PptxGenJS 代码执行失败 (exit {result.returncode}):\n"
                f"stderr: {result.stderr[:800]}\n"
                f"stdout: {result.stdout[:200]}"
            )

        if not os.path.isfile(output_path):
            raise RuntimeError(
                f"代码执行成功但未生成文件: {output_path}\n"
                f"stdout: {result.stdout[:300]}"
            )

        size = os.path.getsize(output_path)
        print(f"[PptxSkill] 生成成功: {output_path} ({size:,} bytes)")
        return output_path

    finally:
        os.unlink(tmp_js)


def read_pptx(path: str) -> str:
    """用 markitdown 提取 .pptx 文本。"""
    try:
        result = subprocess.run(
            ["python", "-m", "markitdown", path],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception as e:
        logger.warning(f"[PptxSkill] markitdown 失败: {e}")
        return ""


def _find_binary(name: str) -> str | None:
    """在常见路径中查找可执行文件，解决 conda 环境 PATH 不含 /opt/homebrew/bin 的问题。"""
    import shutil
    found = shutil.which(name)
    if found:
        return found
    for d in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]:
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _find_libreoffice_app_soffice() -> str | None:
    """在 macOS 上优先定位 LibreOffice.app 内部真实的 soffice 可执行文件。"""
    candidates = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/Applications/OpenOffice.app/Contents/MacOS/soffice",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def get_preview_runtime_diagnostics() -> dict[str, str | bool]:
    """返回缩略图预览依赖的运行时探测结果，便于上层生成用户可读提示。"""
    app_soffice = _find_libreoffice_app_soffice() if sys.platform == "darwin" else None
    soffice_bin = app_soffice or _find_binary("soffice")
    pdftoppm_bin = _find_binary("pdftoppm")
    return {
        "platform": sys.platform,
        "soffice_found": bool(soffice_bin),
        "soffice_path": soffice_bin or "",
        "pdftoppm_found": bool(pdftoppm_bin),
        "pdftoppm_path": pdftoppm_bin or "",
    }


def _build_soffice_convert_commands(pptx_path: str, output_dir: str) -> list[list[str]]:
    """构造一组可依次尝试的 soffice 转 PDF 命令。"""
    profile_dir = tempfile.mkdtemp(prefix="pptagent_lo_profile_")
    convert_args = [
        f"-env:UserInstallation=file://{profile_dir}",
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--nolockcheck",
        "--convert-to",
        "pdf:impress_pdf_Export",
        "--outdir",
        output_dir,
        pptx_path,
    ]

    commands: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def add(command: list[str]) -> None:
        key = tuple(command)
        if key not in seen:
            seen.add(key)
            commands.append(command)

    soffice_bin = _find_binary("soffice")

    if sys.platform == "darwin":
        app_soffice = _find_libreoffice_app_soffice()
        if app_soffice:
            add(["open", "-g", "-W", "-n", "-a", "LibreOffice", "--args", *convert_args])
            add([app_soffice, *convert_args])

    if soffice_bin:
        add([soffice_bin, *convert_args])

    return commands


def pptx_to_images(pptx_path: str, output_dir: str = None) -> list[str]:
    """
    用 soffice 将 .pptx 转 PDF，再用 pdftoppm 转图片。
    macOS 上优先通过 `open -a LibreOffice --args ...` 启动应用，
    避免直接从后台 CLI 拉起 `soffice` 时在 AppKit 初始化阶段崩溃。
    """
    pdftoppm_bin = _find_binary("pdftoppm")
    preview_dir = output_dir or _default_preview_dir(pptx_path)
    soffice_commands = _build_soffice_convert_commands(pptx_path, preview_dir)
    if not soffice_commands:
        logger.warning("[PptxSkill] 未找到 soffice，跳过图片转换")
        return []
    if not pdftoppm_bin:
        logger.warning("[PptxSkill] 未找到 pdftoppm，跳过图片转换")
        return []

    if output_dir is None:
        output_dir = preview_dir
    os.makedirs(output_dir, exist_ok=True)
    _cleanup_preview_images(output_dir)

    pdf_path = os.path.join(output_dir, "temp.pdf")

    try:
        env = os.environ.copy()
        if sys.platform != "darwin":
            env["SAL_USE_VCLPLUGIN"] = "svp"

        base = os.path.splitext(os.path.basename(pptx_path))[0]
        generated_pdf = os.path.join(output_dir, f"{base}.pdf")
        last_failure = ""

        for command in soffice_commands:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )

            if result.returncode == 0 and os.path.isfile(generated_pdf):
                if os.path.isfile(pdf_path):
                    os.unlink(pdf_path)
                os.rename(generated_pdf, pdf_path)
                break

            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            last_failure = stderr or stdout or f"exit {result.returncode}"
            logger.warning(
                "[PptxSkill] soffice 转 PDF 失败（命令: %s, exit %s）: %s",
                os.path.basename(command[0]),
                result.returncode,
                last_failure[:300],
            )
        else:
            logger.warning(f"[PptxSkill] soffice 转 PDF 最终失败: {last_failure[:300]}")
            return []
    except Exception as e:
        logger.warning(f"[PptxSkill] PPTX→PDF 失败: {e}")
        return []

    if not os.path.isfile(pdf_path):
        logger.warning("[PptxSkill] PDF 文件未生成")
        return []

    try:
        prefix = os.path.join(output_dir, "slide")
        result = subprocess.run(
            [pdftoppm_bin, "-jpeg", "-r", "150", pdf_path, prefix],
            capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            logger.warning(f"[PptxSkill] pdftoppm 转图片失败 (exit {result.returncode})")
            return []
    except Exception as e:
        logger.warning(f"[PptxSkill] PDF→图片 失败: {e}")
        return []
    finally:
        if os.path.isfile(pdf_path):
            os.unlink(pdf_path)

    images = _collect_slide_images(output_dir)
    print(f"[PptxSkill] 生成 {len(images)} 张预览图")
    return images
