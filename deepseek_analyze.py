#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek API 转录内容分析脚本
==============================
读取 downloads/ 目录中的 .txt 转录文件，
调用 DeepSeek API（兼容 OpenAI 格式）生成结构化分析报告，
保存为 {原文件名}_deepseek.txt。

依赖：openai（pip install openai）
      DeepSeek 与 OpenAI SDK 完全兼容，只需切换 base_url

.env 配置：
    DEEPSEEK_API_KEY="sk-你的key"
"""

import os
import re
import sys
import time
from pathlib import Path

from openai import OpenAI, AuthenticationError, RateLimitError, APIError

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────

DOWNLOADS_DIR = Path(__file__).parent / "downloads"
DOTENV_FILE   = Path(__file__).parent / ".env"
API_KEY_ENV   = "DEEPSEEK_API_KEY"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL             = "deepseek-chat"   # DeepSeek-V3，性价比最高

SYSTEM_PROMPT_DOUYIN = (
    "你是一位专业的短视频内容分析师，专注于餐饮和创业领域。"
    "请用简洁、结构化的中文输出分析结果。输出必须是 Markdown（带标题/列表/表格），不要输出代码块围栏，不要复述用户指令。"
)

# 兼容旧名称
SYSTEM_PROMPT = SYSTEM_PROMPT_DOUYIN

USER_PROMPT_TEMPLATE_DOUYIN = """\
以下是一段从抖音短视频转录的中文文字（语音转文字，可能缺少标点符号）。请输出一份 **Markdown** 分析报告，遵循以下格式与要求：

- 使用二级标题 `##` 作为一级章节标题（不要用 `#`）。
- 关键信息尽量用列表与表格呈现，避免大段堆叠。
- 每个章节优先给结论，再给依据。
- 不要输出任何代码块围栏。

## 一、视频主要观点总结
用 3-5 句完整的话概括视频的核心论点和最终结论，覆盖全文重点。

## 二、关键信息提取
按以下类别输出（建议用小标题 `###` + 列表或表格）：
- 数据/比例：视频中出现的数字、百分比、规模数据（尽量做成表格：字段/数值/语境）
- 品牌/产品：提到的具体品牌或产品名称（列表）
- 核心方法论：作者提出的具体操作标准或判断框架（列表）
- 风险提示：明确指出的坑、陷阱或反面案例（列表）

## 三、对茶饮创业者的参考价值
- 评分：X/10
- 评分理由：3-4 点
- 最适合人群：1-2 句

## 四、为博主提供的内容优化建议
### 4.1 脚本优化方向（3-5 点）
每条建议后用括号补充原因。

### 4.2 参考开头脚本（80-120 字）
要求：口语化、节奏快、前 3 秒强钩子；紧贴原主题，不要凭空编造。

### 4.3 整体结构建议（3-5 点）
用 bullet 要点说明开头-展开-结尾应如何组织；每条后用括号写原因。

---
转录文字：
{transcript}
"""

# 兼容：历史代码里 USER_PROMPT_TEMPLATE 指向抖音模板
USER_PROMPT_TEMPLATE = USER_PROMPT_TEMPLATE_DOUYIN


SYSTEM_PROMPT_YOUTUBE = (
    "你是一位熟悉 YouTube 长视频与知识类/商业类频道的增长与内容策略顾问。"
    "转录可能为中英混杂或 ASR 错误，请在理解意图后分析。"
    "输出必须是 Markdown（## / ### / 列表 / 表格），不要代码块围栏，不要复述本指令。"
)

USER_PROMPT_TEMPLATE_YOUTUBE = """\
以下是一段从 **YouTube 视频** 转录得到的文字（自动语音识别，可能有错字、缺标点、中英混杂）。请输出一份 **Markdown** 分析报告。
注意：这是 YouTube 场景，**不要**按抖音「前 3 秒强钩子、短平快口播」那套硬套；应更关注信息结构、论证节奏、系列价值与搜索/订阅转化。

## 一、视频定位与核心论点
- 这条视频在解决什么问题、面向谁（受众层级：入门/进阶）？
- 用 3-6 句话概括核心结论与边界（作者明确说了什么 / 没说什么）。

## 二、叙事与结构拆解
用 `###` 小标题分别写：**开场** → **主体论证/演示** → **收尾与行动号召**。
- 每段说明：功能（建立预期、举证、过渡、总结）+ 可改进点（1 条即可）。

## 三、信息密度与可信度
- 关键概念/数据/案例：列表或表格列出（如有）。
- 若存在跳跃、未定义术语或论证薄弱处，指出并给「补一句就能更稳」的建议。

## 四、YouTube 侧可执行建议（重点）
### 4.1 标题与封面方向
- 给出 3 个**不同角度**的标题草案（偏搜索 / 偏好奇 / 偏系列编号），并各用一句话说适用场景。

### 4.2 描述区与关键词
- 建议描述开头 2-3 行（可直接粘贴改写的草稿）。
- 列出 8-12 个相关搜索词或话题标签方向（不必加 #）。

### 4.3 系列化与复用
- 若适合做成系列：建议系列名 + 下 2 集选题方向（与原文主题强相关）。

## 五、合规与表达风险（简要）
- 若有明显版权、医疗/金融投资建议、夸大表述等风险，单列提醒；若无则写「未发现明显风险点」。

---
转录文字：
{transcript}
"""

# ──────────────────────────────────────────────
# 全文阅读（逐字稿整理：翻译 / 标点 / ASR 纠偏）
# ──────────────────────────────────────────────

SYSTEM_PROMPT_READABLE = (
    "你是专业的语音转写稿整理助手。根据用户提供的自动转录文字，输出**仅整理后的可读正文**。"
    "使用规范简体中文。不要输出前言、标题层级、Markdown、代码块或复述本指令。"
)

USER_PROMPT_READABLE_CHUNK = """请对以下语音转录稿进行处理，输出一段便于阅读的连续正文（可适当分段，段间用空行分隔）：

1. 若原文以英文为主，请翻译成流畅的中文；中英混杂时以中文表述为主，必要专有名词可保留英文。
2. 若缺少标点或断句困难，请补全合适的中文标点（。，！？、；：“”‘’等），合理分段。
3. 若有明显同音错字、方言或吐字不清造成的误识，请结合上下文推断并修正；极不确定处尽量少改并保留原意。
4. 保持说话顺序与信息完整，不要写摘要、不要点评、不要编造原稿没有的内容。

{part_hint}
---
转录稿：
{transcript}
"""

READABLE_CHUNK_CHARS = 12000
READABLE_MAX_TOKENS = 8192


def _split_readable_chunks(text: str, max_chars: int = READABLE_CHUNK_CHARS) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paras = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    buf: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buf, size
        if buf:
            chunks.append("\n\n".join(buf))
            buf = []
            size = 0

    for p in paras:
        p = p.strip()
        if not p:
            continue
        if len(p) > max_chars:
            flush()
            chunks.extend(_hard_split_long_paragraph(p, max_chars))
            continue
        if buf and size + 2 + len(p) > max_chars:
            flush()
        buf.append(p)
        size = len("\n\n".join(buf))
    flush()
    return [c for c in chunks if c.strip()]


def _hard_split_long_paragraph(p: str, max_chars: int) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(p)
    while i < n:
        end = min(i + max_chars, n)
        raw_slice = p[i:end]
        advance = len(raw_slice)
        chunk = raw_slice
        if end < n:
            break_at = max(
                chunk.rfind("。"),
                chunk.rfind("！"),
                chunk.rfind("？"),
                chunk.rfind(".\n"),
                chunk.rfind(". "),
                chunk.rfind("\n"),
            )
            if break_at > max_chars // 3:
                chunk = raw_slice[: break_at + 1]
                advance = len(chunk)
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
        i += max(advance, 1)
    return out


def _polish_one_chunk(
    client: OpenAI,
    transcript: str,
    *,
    part: int,
    total: int,
) -> str:
    if total > 1:
        part_hint = (
            f"（说明：整稿共分 {total} 段，当前为第 {part} 段，只处理本段文字，不要重复前段内容。）"
        )
    else:
        part_hint = ""

    prompt = USER_PROMPT_READABLE_CHUNK.format(part_hint=part_hint, transcript=transcript)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_READABLE},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=READABLE_MAX_TOKENS,
    )
    usage = response.usage
    if usage:
        cost_yuan = (usage.prompt_tokens * 0.27 + usage.completion_tokens * 1.1) / 1_000_000
        print(
            f"   [全文阅读] Token：入 {usage.prompt_tokens} + 出 {usage.completion_tokens}"
            f"  ≈ ¥{cost_yuan:.5f}  （段 {part}/{total}）"
        )
    raw = (response.choices[0].message.content or "").strip()
    return raw


def polish_transcript_for_reading(api_key: str, transcript: str) -> str:
    """
    调用 DeepSeek 将 ASR 转录整理为可读中文稿（翻译 / 标点 / 上下文纠偏）。
    长文自动分段请求后拼接。
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return ""

    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    chunks = _split_readable_chunks(transcript)
    if not chunks:
        return ""

    print(f"   [全文阅读] 模型：{MODEL}，共 {len(chunks)} 段，原文约 {len(transcript):,} 字")

    parts: list[str] = []
    for i, ch in enumerate(chunks, 1):
        print(f"   [全文阅读] 正在处理第 {i}/{len(chunks)} 段...")
        parts.append(_polish_one_chunk(client, ch, part=i, total=len(chunks)))
    return "\n\n".join(p for p in parts if p.strip()).strip()


def save_readable_transcript(raw_txt_path: Path, polished: str) -> Path:
    """保存为与转录同 stem 的 ``*_readable.txt``。"""
    out_path = raw_txt_path.with_name(raw_txt_path.stem + "_readable.txt")
    meta = (
        f"<!--\n"
        f"全文阅读（DeepSeek 整理稿）\n"
        f"来源转录：{raw_txt_path.name}\n"
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"-->\n\n"
    )
    out_path.write_text(meta + polished.strip() + "\n", encoding="utf-8")
    return out_path


def _prompts_for_mode(analysis_mode: str) -> tuple[str, str]:
    m = (analysis_mode or "douyin").lower().strip()
    if m == "youtube":
        return SYSTEM_PROMPT_YOUTUBE, USER_PROMPT_TEMPLATE_YOUTUBE
    return SYSTEM_PROMPT_DOUYIN, USER_PROMPT_TEMPLATE_DOUYIN


# ──────────────────────────────────────────────
# .env 加载
# ──────────────────────────────────────────────

def load_dotenv():
    """把 .env 文件的 KEY=VALUE 载入环境变量（不覆盖已有值）"""
    if not DOTENV_FILE.exists():
        return
    for line in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def get_api_key() -> str:
    """
    获取 DeepSeek API Key，优先级：
    1. 环境变量 DEEPSEEK_API_KEY
    2. .env 文件
    3. 交互式输入（可选择保存）
    """
    load_dotenv()
    key = os.environ.get(API_KEY_ENV, "").strip()
    if key:
        print("✅ 从 .env 读取 DeepSeek API Key")
        return key

    print("\n未检测到 DeepSeek API Key。")
    print("注册地址：https://platform.deepseek.com  （国内手机号，充值 1 元即可）")
    key = input("请输入 API Key（sk-...）：").strip()
    if not key:
        print("❌ 未输入 API Key，退出")
        sys.exit(1)

    save = input("是否保存到 .env 文件？(y/N)：").strip().lower()
    if save == "y":
        lines = DOTENV_FILE.read_text(encoding="utf-8").splitlines() \
                if DOTENV_FILE.exists() else []
        lines = [l for l in lines if not l.startswith(f"{API_KEY_ENV}=")]
        lines.append(f'{API_KEY_ENV}="{key}"')
        DOTENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"✅ 已保存到 {DOTENV_FILE.name}")

    return key


# ──────────────────────────────────────────────
# 读取转录文本
# ──────────────────────────────────────────────

def read_transcript(txt_path: Path) -> str:
    """读取转录文件，跳过文件头注释行，返回正文"""
    raw = txt_path.read_text(encoding="utf-8")
    lines = []
    header_done = False
    for line in raw.splitlines():
        if not header_done:
            if line.startswith("#") or line.startswith("─") or line.strip() == "":
                continue
            header_done = True
        lines.append(line)
    return "\n".join(lines).strip()


# ──────────────────────────────────────────────
# DeepSeek API 调用
# ──────────────────────────────────────────────

def analyze_with_deepseek(api_key: str, transcript: str, analysis_mode: str = "douyin") -> str:
    """
    调用 DeepSeek API 分析转录文本。

    :param analysis_mode: douyin（短视频模板）| youtube（长视频/YouTube 模板）
    """
    client = OpenAI(
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
    )

    system_prompt, user_tmpl = _prompts_for_mode(analysis_mode)
    prompt = user_tmpl.format(transcript=transcript)
    max_tokens = 4096 if (analysis_mode or "").lower().strip() == "youtube" else 2048

    print(f"   模型：{MODEL}")
    print(f"   分析模式：{(analysis_mode or 'douyin').lower()}")
    print(f"   文本长度：{len(transcript):,} 字")
    print("   正在调用 DeepSeek API...")

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.3,      # 低随机性，分析更稳定
        max_tokens=max_tokens,
    )

    # 显示本次消耗的 token 数（方便估算费用）
    usage = response.usage
    if usage:
        cost_yuan = (usage.prompt_tokens * 0.27 + usage.completion_tokens * 1.1) / 1_000_000
        print(f"   Token 消耗：输入 {usage.prompt_tokens} + 输出 {usage.completion_tokens}"
              f"  ≈ ¥{cost_yuan:.5f}")

    return response.choices[0].message.content


# ──────────────────────────────────────────────
# 报告保存
# ──────────────────────────────────────────────

def save_report(
    txt_path: Path, analysis: str, transcript_len: int, analysis_mode: str = "douyin"
) -> Path:
    """保存分析报告为 {原文件名}_deepseek.txt"""
    out_path = txt_path.with_name(txt_path.stem + "_deepseek.txt")
    meta = (
        f"<!--\n"
        f"DeepSeek AI 分析报告\n"
        f"分析模式：{(analysis_mode or 'douyin').lower()}\n"
        f"来源转录文件：{txt_path.name}\n"
        f"分析时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"使用模型：{MODEL}\n"
        f"原文字数：{transcript_len:,} 字\n"
        f"-->\n\n"
    )
    out_path.write_text(meta + analysis, encoding="utf-8")
    return out_path


# ──────────────────────────────────────────────
# 对外接口（供 main.py 调用）
# ──────────────────────────────────────────────

def analyze_file(
    txt_path: Path, api_key: str | None = None, analysis_mode: str = "douyin"
) -> Path | None:
    """
    分析单个转录 txt 文件，生成 _deepseek.txt 报告。
    :param analysis_mode: douyin | youtube
    :return: 报告文件路径，失败返回 None
    """
    if api_key is None:
        api_key = get_api_key()

    mode = (analysis_mode or "douyin").lower().strip()
    if mode not in ("douyin", "youtube"):
        mode = "douyin"

    transcript = read_transcript(txt_path)
    if not transcript:
        print(f"  ⚠️  文件为空，跳过：{txt_path.name}")
        return None

    print(f"\n{'─' * 54}")
    print(f"  📂 分析：{txt_path.name}  （模式：{mode}）")

    t0 = time.time()
    try:
        analysis = analyze_with_deepseek(api_key, transcript, analysis_mode=mode)
    except AuthenticationError:
        print("  ❌ API Key 无效，请检查 .env 中的 DEEPSEEK_API_KEY")
        return None
    except RateLimitError:
        print("  ❌ 余额不足或触发限速，请登录 https://platform.deepseek.com 检查账户")
        return None
    except APIError as e:
        print(f"  ❌ API 错误：{e}")
        return None
    except Exception as e:
        print(f"  ❌ 调用失败：{e}")
        return None

    elapsed = time.time() - t0
    out_path = save_report(txt_path, analysis, len(transcript), analysis_mode=mode)
    print(f"  ✅ 完成，耗时 {elapsed:.1f}s")
    print(f"  💾 已保存：{out_path.name}")
    return out_path


# ──────────────────────────────────────────────
# 独立运行入口
# ──────────────────────────────────────────────

def main():
    print("=" * 54)
    print("  🤖  DeepSeek AI 内容分析")
    print("=" * 54)

    api_key = get_api_key()

    if len(sys.argv) > 1:
        txt_files = [Path(p) for p in sys.argv[1:]
                     if Path(p).suffix == ".txt" and "_deepseek" not in Path(p).stem]
    else:
        if not DOWNLOADS_DIR.exists():
            print(f"❌ 目录不存在：{DOWNLOADS_DIR}")
            sys.exit(1)
        txt_files = [
            f for f in sorted(DOWNLOADS_DIR.glob("*.txt"))
            if not any(tag in f.stem for tag in ("_deepseek", "_gemini", "_analysis", "_summary"))
        ]

    if not txt_files:
        print("❌ 未找到待分析的转录文件")
        sys.exit(1)

    pending = [f for f in txt_files
               if not f.with_name(f.stem + "_deepseek.txt").exists()]
    skipped = [f for f in txt_files if f not in pending]

    if skipped:
        print(f"\n⏭  已有报告，跳过 {len(skipped)} 个（删除 _deepseek.txt 可重新生成）")
    if not pending:
        print("\n✅ 所有文件均已分析完成")
        return

    print(f"\n📋 待分析：{len(pending)} 个文件")
    for i, f in enumerate(pending, 1):
        print(f"   {i}. {f.name}")

    success = 0
    for f in pending:
        out = analyze_file(f, api_key)
        if out:
            success += 1
            content_lines = [
                l for l in out.read_text(encoding="utf-8").splitlines()
                if not l.startswith("#") and l.strip()
            ]
            print("\n  📄 内容预览：")
            for line in content_lines[:10]:
                print(f"  {line}")

    print(f"\n{'=' * 54}")
    print(f"🎉  完成！成功分析 {success}/{len(pending)} 个文件")
    if success:
        print(f"📁  结果保存在：{DOWNLOADS_DIR.resolve()}/")
    print("=" * 54)


if __name__ == "__main__":
    main()
