#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude API 转录内容分析脚本
===========================
读取 downloads/ 目录中的 .txt 转录文件，
调用 Claude API 生成结构化总结分析，
并保存为同名 _summary.txt 文件。

使用方法：
    # 分析所有未处理的 txt 文件
    python summarize_transcript.py

    # 指定单个文件
    python summarize_transcript.py downloads/xxx.txt

配置 API Key（三选一，优先级从高到低）：
    1. 环境变量：export ANTHROPIC_API_KEY=your_key
    2. .env 文件：ANTHROPIC_API_KEY=your_key
    3. 脚本运行时交互式输入
"""

import os
import sys
import time
from pathlib import Path

import anthropic

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────

DOWNLOADS_DIR = Path(__file__).parent / "downloads"
DOTENV_FILE   = Path(__file__).parent / ".env"
API_KEY_ENV   = "ANTHROPIC_API_KEY"

# 使用的 Claude 模型
MODEL = "claude-sonnet-4-6"

# 单次请求最大输出 token 数
MAX_TOKENS = 2048

# 总结提示词
SUMMARY_PROMPT = """\
你是一位专业的内容分析师。下面是一段从短视频（抖音）音频转录来的中文文字，请对其进行深度分析，输出以下几个部分：

---

## 📌 视频主要观点
用 3-5 句话概括视频的核心论点和结论。语言简洁，直击要点。

## 🔑 关键信息梳理
以结构化要点列出视频中提到的重要数据、论据、方法论或建议（6-10 条）。

## 💡 核心逻辑框架
用一段话描述视频的论证思路，帮助读者理解作者是如何一步步得出结论的。

## 🌟 适合转发的亮点金句
从原文中提炼 3-5 句有传播价值的话，每句后面附上简短说明为什么值得转发。

## 📊 内容价值评估
- **信息密度**：高 / 中 / 低
- **实用价值**：★★★★★（5分制）
- **适合人群**：（描述最适合看这个视频的受众）
- **一句话推荐语**：（如果要推荐给朋友，你会怎么说）

---

以下是转录文字内容：

{transcript}
"""


# ──────────────────────────────────────────────
# API Key 加载
# ──────────────────────────────────────────────

def load_api_key() -> str:
    """按优先级加载 Anthropic API Key"""
    # 1. 环境变量
    key = os.environ.get(API_KEY_ENV, "").strip()
    if key:
        print("✅ 从环境变量读取 Anthropic API Key")
        return key

    # 2. .env 文件
    if DOTENV_FILE.exists():
        for line in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{API_KEY_ENV}="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    print("✅ 从 .env 文件读取 Anthropic API Key")
                    return key

    # 3. 交互式输入
    print("\n未检测到 Anthropic API Key。")
    print("获取地址：https://console.anthropic.com/settings/keys")
    key = input("请输入 API Key（sk-ant-...）: ").strip()
    if not key:
        print("❌ 未输入 API Key，退出")
        sys.exit(1)

    save = input("是否保存到 .env 文件以便下次使用？(y/N): ").strip().lower()
    if save == "y":
        lines = DOTENV_FILE.read_text(encoding="utf-8").splitlines() if DOTENV_FILE.exists() else []
        lines = [l for l in lines if not l.startswith(f"{API_KEY_ENV}=")]
        lines.append(f'{API_KEY_ENV}="{key}"')
        DOTENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"✅ API Key 已保存到 {DOTENV_FILE.name}")

    return key


# ──────────────────────────────────────────────
# 读取转录文本
# ──────────────────────────────────────────────

def read_transcript(txt_path: Path) -> str:
    """
    读取 .txt 转录文件，自动跳过文件头注释行（以 # 或 ─ 开头的行）。
    返回纯文本内容。
    """
    raw = txt_path.read_text(encoding="utf-8")
    lines = []
    in_header = True
    for line in raw.splitlines():
        # 跳过文件头（# 注释行和分隔线）
        if in_header and (line.startswith("#") or line.startswith("─") or line.strip() == ""):
            continue
        in_header = False
        lines.append(line)
    return "\n".join(lines).strip()


# ──────────────────────────────────────────────
# Claude API 调用
# ──────────────────────────────────────────────

def summarize_with_claude(client: anthropic.Anthropic, transcript: str) -> str:
    """
    调用 Claude API 对转录文本进行结构化总结分析。

    使用 Messages API，单轮对话模式：
    - system：设定分析师角色
    - user：包含提示词和转录内容

    :param client:     Anthropic 客户端实例
    :param transcript: 转录文本内容
    :return:           Claude 生成的总结文本
    """
    prompt = SUMMARY_PROMPT.format(transcript=transcript)

    print(f"   模型: {MODEL}")
    print(f"   文本长度: {len(transcript):,} 字")
    print("   正在调用 Claude API...")

    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=(
            "你是一位专业的内容分析师，擅长从口语化的短视频文字中提炼核心观点，"
            "输出结构清晰、适合分享的内容总结。用中文回答，格式规范。"
        ),
        messages=[
            {"role": "user", "content": prompt}
        ],
    )

    return message.content[0].text


def save_summary(txt_path: Path, summary: str, transcript_length: int) -> Path:
    """
    将总结保存为 <原文件名>_summary.txt。
    文件头附上元信息，方便追溯来源。
    """
    summary_path = txt_path.with_name(txt_path.stem + "_summary.txt")

    header = (
        f"# AI 分析总结\n"
        f"# 来源转录文件: {txt_path.name}\n"
        f"# 分析时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# 使用模型: {MODEL}\n"
        f"# 原文字数: {transcript_length:,} 字\n"
        f"{'─' * 55}\n\n"
    )

    summary_path.write_text(header + summary, encoding="utf-8")
    return summary_path


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

def main() -> None:
    print("=" * 55)
    print("  🤖  Claude AI 内容分析  (转录文本总结)")
    print("=" * 55)

    # 1. 加载 API Key，初始化客户端
    api_key = load_api_key()
    client  = anthropic.Anthropic(api_key=api_key)

    # 2. 确定待处理文件列表
    if len(sys.argv) > 1:
        # 命令行指定文件
        txt_files = [Path(p) for p in sys.argv[1:] if Path(p).suffix == ".txt"]
        # 排除已是 summary 文件的
        txt_files = [f for f in txt_files if not f.stem.endswith("_summary")]
        if not txt_files:
            print("❌ 未指定有效的 .txt 文件")
            sys.exit(1)
    else:
        # 自动扫描 downloads/ 目录
        if not DOWNLOADS_DIR.exists():
            print(f"❌ 目录不存在: {DOWNLOADS_DIR}")
            sys.exit(1)
        all_txt = sorted(DOWNLOADS_DIR.glob("*.txt"))
        # 只处理转录文件（不处理已生成的 summary 文件）
        txt_files = [f for f in all_txt if not f.stem.endswith("_summary")]

    if not txt_files:
        print(f"❌ 未找到转录文件（{DOWNLOADS_DIR}/*.txt）")
        sys.exit(1)

    # 跳过已有 summary 的文件
    pending = []
    skipped = []
    for f in txt_files:
        summary_path = f.with_name(f.stem + "_summary.txt")
        if summary_path.exists():
            skipped.append(f)
        else:
            pending.append(f)

    if skipped:
        print(f"\n⏭  已有总结，跳过（删除对应 _summary.txt 可重新生成）：")
        for f in skipped:
            print(f"   {f.name}")

    if not pending:
        print("\n✅ 所有文件均已分析完成")
        return

    print(f"\n📋 待分析文件: {len(pending)} 个")
    for i, f in enumerate(pending, 1):
        print(f"   {i}. {f.name}")

    # 3. 逐个分析
    success = 0
    for i, txt_path in enumerate(pending, 1):
        print(f"\n{'─' * 55}")
        print(f"[{i}/{len(pending)}] 分析: {txt_path.name}")

        # 读取转录文本
        transcript = read_transcript(txt_path)
        if not transcript:
            print("⚠️  文件为空，跳过")
            continue

        # 调用 Claude
        t0 = time.time()
        try:
            summary = summarize_with_claude(client, transcript)
            elapsed = time.time() - t0
            print(f"   ✅ 完成，耗时 {elapsed:.1f}s")
        except anthropic.AuthenticationError:
            print("❌ API Key 无效，请检查后重试")
            sys.exit(1)
        except anthropic.RateLimitError:
            print("❌ 触发速率限制，请稍后再试")
            sys.exit(1)
        except Exception as e:
            print(f"❌ 调用失败: {e}")
            continue

        # 保存总结
        summary_path = save_summary(txt_path, summary, len(transcript))
        print(f"   💾 已保存: {summary_path.name}")
        success += 1

        # 预览总结前几行
        preview_lines = summary.strip().splitlines()[:8]
        print("\n   📄 总结预览:")
        for line in preview_lines:
            print(f"   {line}")
        print("   ...")

    # 4. 汇总
    print(f"\n{'=' * 55}")
    print(f"🎉  完成！成功分析 {success}/{len(pending)} 个文件")
    if success > 0:
        print(f"📁  结果保存在: {DOWNLOADS_DIR.resolve()}/")
    print("=" * 55)


if __name__ == "__main__":
    main()
