#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini API 转录内容分析脚本
============================
读取 downloads/ 目录中的 .txt 转录文件，
调用 Google Gemini API 生成结构化分析报告，
保存为 {原文件名}_gemini.txt。

依赖：google-genai（pip install google-genai）
API Key：在 .env 文件中设置 GEMINI_API_KEY=你的key
"""

import os
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────

DOWNLOADS_DIR = Path(__file__).parent / "downloads"
DOTENV_FILE   = Path(__file__).parent / ".env"
API_KEY_ENV   = "GEMINI_API_KEY"

# 使用 Gemini 2.0 Flash（免费额度最高，速度快）
MODEL = "gemini-2.0-flash"

# 分析提示词
PROMPT_TEMPLATE = """\
你是一位专业的短视频内容分析师，专注于餐饮和创业领域。
以下是一段从抖音短视频转录的中文文字（无标点），请仔细阅读后完成以下分析：

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【一、视频主要观点总结】
用 3-5 句完整的话，概括视频的核心论点和最终结论。语言简洁，覆盖全文重点。

【二、关键信息提取】
提取视频中出现的具体信息，按以下类别分点列出：
- 📊 数据/比例：（如：90%新开店不挣钱、15万家倒闭等）
- 🏪 品牌/产品：（提到的具体品牌名）
- 💡 核心方法论：（作者提出的选品牌/选位置的具体标准）
- ⚠️ 风险提示：（明确指出的坑和陷阱）

【三、对茶饮创业者的参考价值评分】
- 评分：X/10
- 评分理由：分 3-4 点说明，要具体（如：包含XX数据、提供XX操作步骤等）
- 建议人群：哪类人最值得看这个视频

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
请直接输出分析内容，不要重复上面的格式说明。

转录文字如下：
{transcript}
"""


# ──────────────────────────────────────────────
# .env 加载
# ──────────────────────────────────────────────

def load_dotenv():
    """把 .env 文件的 KEY=VALUE 载入 os.environ（不覆盖已有环境变量）"""
    if not DOTENV_FILE.exists():
        return
    for line in DOTENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def get_api_key() -> str:
    """
    获取 Gemini API Key，优先级：
    1. 环境变量 GEMINI_API_KEY
    2. .env 文件
    3. 交互式输入（可选择保存到 .env）
    """
    load_dotenv()
    key = os.environ.get(API_KEY_ENV, "").strip()
    if key:
        print(f"✅ 从 .env / 环境变量读取 Gemini API Key")
        return key

    print("\n未检测到 Gemini API Key。")
    print("获取地址：https://aistudio.google.com/app/apikey（免费，无需信用卡）")
    key = input("请输入 API Key（AIzaSy...）：").strip()
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
# 文本读取
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
# Gemini API 调用
# ──────────────────────────────────────────────

def analyze_with_gemini(api_key: str, transcript: str) -> str:
    """
    调用 Gemini API 分析转录文本。

    使用新版 google-genai SDK：
    - client = genai.Client(api_key=...)
    - client.models.generate_content(model, contents)

    :param api_key:    Gemini API Key
    :param transcript: 转录文本内容
    :return:           Gemini 生成的分析文本
    """
    client = genai.Client(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(transcript=transcript)

    print(f"   模型：{MODEL}")
    print(f"   文本长度：{len(transcript):,} 字")
    print("   正在调用 Gemini API...")

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,        # 低随机性，保证分析准确稳定
            max_output_tokens=2048,
        ),
    )
    return response.text


# ──────────────────────────────────────────────
# 报告保存
# ──────────────────────────────────────────────

def save_report(txt_path: Path, analysis: str, transcript_len: int) -> Path:
    """
    保存分析报告为 {原文件名}_gemini.txt。
    文件头附带元信息，方便追溯。
    """
    out_path = txt_path.with_name(txt_path.stem + "_gemini.txt")

    header = (
        f"# Gemini AI 分析报告\n"
        f"# 来源转录文件：{txt_path.name}\n"
        f"# 分析时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# 使用模型：{MODEL}\n"
        f"# 原文字数：{transcript_len:,} 字\n"
        f"{'─' * 54}\n\n"
    )
    out_path.write_text(header + analysis, encoding="utf-8")
    return out_path


# ──────────────────────────────────────────────
# 对外接口（供 main.py 调用）
# ──────────────────────────────────────────────

def analyze_file(txt_path: Path, api_key: str | None = None) -> Path | None:
    """
    分析单个转录 txt 文件，生成 _gemini.txt 报告。
    :return: 报告文件路径，失败返回 None
    """
    if api_key is None:
        api_key = get_api_key()

    transcript = read_transcript(txt_path)
    if not transcript:
        print(f"  ⚠️  文件为空，跳过：{txt_path.name}")
        return None

    print(f"\n{'─' * 54}")
    print(f"  📂 分析：{txt_path.name}")

    t0 = time.time()
    try:
        analysis = analyze_with_gemini(api_key, transcript)
    except Exception as e:
        # 细化常见错误提示
        err = str(e)
        if "API_KEY_INVALID" in err or "invalid" in err.lower():
            print(f"  ❌ API Key 无效，请检查 .env 中的 GEMINI_API_KEY")
        elif "limit: 0" in err:
            print(f"  ❌ 该 Google 项目未开通 Gemini 免费配额（limit: 0）")
            print(f"     解决方法：")
            print(f"     方案A（推荐）：前往 https://console.cloud.google.com/billing")
            print(f"                   为该项目绑定信用卡（不会自动扣费，仅用于解锁配额）")
            print(f"     方案B：在 Google AI Studio 重新创建 API Key")
            print(f"            https://aistudio.google.com/app/apikey")
            print(f"            确保选择 [在新项目中创建 API Key]")
        elif "quota" in err.lower() or "429" in err:
            import re
            delay = re.search(r"retry in (\d+)", err)
            wait  = delay.group(1) if delay else "60"
            print(f"  ❌ 速率限制，建议等待 {wait} 秒后重试")
        elif "SAFETY" in err:
            print(f"  ❌ 内容被安全过滤器拦截")
        else:
            print(f"  ❌ 调用失败：{e}")
        return None

    elapsed = time.time() - t0
    out_path = save_report(txt_path, analysis, len(transcript))
    print(f"  ✅ 完成，耗时 {elapsed:.1f}s")
    print(f"  💾 已保存：{out_path.name}")
    return out_path


# ──────────────────────────────────────────────
# 独立运行入口
# ──────────────────────────────────────────────

def main():
    print("=" * 54)
    print("  🤖  Gemini AI 内容分析  (google-genai)")
    print("=" * 54)

    api_key = get_api_key()

    # 确定待处理文件
    if len(sys.argv) > 1:
        txt_files = [Path(p) for p in sys.argv[1:]
                     if Path(p).suffix == ".txt" and "_gemini" not in Path(p).stem]
    else:
        if not DOWNLOADS_DIR.exists():
            print(f"❌ 目录不存在：{DOWNLOADS_DIR}")
            sys.exit(1)
        txt_files = [
            f for f in sorted(DOWNLOADS_DIR.glob("*.txt"))
            if "_gemini" not in f.stem
            and "_analysis" not in f.stem
            and "_summary" not in f.stem
        ]

    if not txt_files:
        print("❌ 未找到待分析的转录文件")
        sys.exit(1)

    # 跳过已有报告的文件
    pending = [f for f in txt_files
               if not f.with_name(f.stem + "_gemini.txt").exists()]
    skipped = [f for f in txt_files if f not in pending]

    if skipped:
        print(f"\n⏭  已有报告，跳过 {len(skipped)} 个（删除 _gemini.txt 可重新生成）")
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
            # 预览前 10 行
            preview = out.read_text(encoding="utf-8").splitlines()
            content_lines = [l for l in preview if not l.startswith("#") and l.strip()]
            print("\n  📄 内容预览：")
            for line in content_lines[:8]:
                print(f"  {line}")

    print(f"\n{'=' * 54}")
    print(f"🎉  完成！成功分析 {success}/{len(pending)} 个文件")
    if success:
        print(f"📁  结果保存在：{DOWNLOADS_DIR.resolve()}/")
    print("=" * 54)


if __name__ == "__main__":
    main()
