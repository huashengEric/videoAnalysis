#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 ASR 音频转文字脚本（faster-whisper 服务）
==========================================
将 downloads/ 目录中的 MP3 文件通过 HTTP 上传到本地 ASR 服务，
转录为中文文本并保存为同名 .txt 文件。

使用方法：
    # 转录所有 MP3（使用本地 ASR 服务）
    python transcribe_audio.py

    # 指定某个文件
    python transcribe_audio.py downloads/xxx.mp3

依赖：
    1. 本地已启动 ASR 服务（例如 local-asr-service）：
         uvicorn main:app --host 127.0.0.1 --port 9000
    2. 环境可访问 LOCAL_ASR_URL 指定的地址
"""

import os
import sys
import time
from pathlib import Path

import requests

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────

# MP3 所在目录
DOWNLOADS_DIR = Path(__file__).parent / "downloads"

# 本地 ASR 服务配置
LOCAL_ASR_URL = os.environ.get("LOCAL_ASR_URL", "http://127.0.0.1:9000/transcribe")


# ──────────────────────────────────────────────
# 本地 ASR（faster-whisper 服务）转录
# ──────────────────────────────────────────────

def _transcribe_via_local_asr(audio_path: Path) -> str | None:
    """
    调用本地 ASR HTTP 服务进行转写。

    依赖：本机已启动 local-asr-service，例如：
        uvicorn main:app --host 127.0.0.1 --port 9000
    并提供 /transcribe 上传接口。
    """
    print(f"\n{'─' * 55}")
    print(f"📂 文件: {audio_path.name}")
    print(f"   大小: {audio_path.stat().st_size // 1024:,} KB")
    print(f"🌐 使用本地 ASR 服务: {LOCAL_ASR_URL}")

    try:
        with audio_path.open("rb") as f:
            files = {"file": (audio_path.name, f, "audio/mpeg")}
            resp = requests.post(LOCAL_ASR_URL, files=files, timeout=600)
        resp.raise_for_status()
        data = resp.json()
        text = (data.get("text") or "").strip()
        if not text:
            print("❌ 本地 ASR 返回空文本")
            return None
        print(
            f"✅ 本地 ASR 转写完成："
            f"模型={data.get('model_size')}, "
            f"音频时长={data.get('duration')}s, "
            f"耗时={data.get('time_sec')}s"
        )
        return text
    except Exception as e:
        print(f"❌ 本地 ASR 调用失败: {e}")
        return None


# ──────────────────────────────────────────────
# 转录核心（默认 AssemblyAI，可切换本地 ASR）
# ──────────────────────────────────────────────

def transcribe_file(audio_path: Path) -> str | None:
    """
    使用本地 ASR 服务转录单个音频文件。

    :param audio_path: MP3 文件路径
    :return:           转录文本字符串，失败时返回 None
    """
    return _transcribe_via_local_asr(audio_path)


def save_transcript(audio_path: Path, text: str) -> Path:
    """
    将转录文本保存为 .txt 文件（与 MP3 同目录、同名）。

    :param audio_path: 原始 MP3 文件路径
    :param text:       转录文本
    :return:           保存的 .txt 文件路径
    """
    txt_path = audio_path.with_suffix(".txt")

    # 文件头信息
    header = (
        f"# 转录文件\n"
        f"# 来源: {audio_path.name}\n"
        f"# 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# 语言: 中文（本地 ASR 转录）\n"
        f"{'─' * 55}\n\n"
    )

    txt_path.write_text(header + text, encoding="utf-8")
    print(f"💾 已保存: {txt_path.name}")
    return txt_path


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

def main() -> None:
    print("=" * 55)
    print("  🎙  本地 ASR 音频转文字  (faster-whisper 服务)")
    print("=" * 55)

    # 2. 确定要处理的文件列表
    if len(sys.argv) > 1:
        # 命令行指定文件
        mp3_files = [Path(p) for p in sys.argv[1:] if Path(p).suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg")]
        if not mp3_files:
            print("❌ 未找到有效的音频文件（支持 .mp3/.m4a/.wav/.ogg）")
            sys.exit(1)
    else:
        # 自动扫描 downloads/ 目录
        if not DOWNLOADS_DIR.exists():
            print(f"❌ 目录不存在: {DOWNLOADS_DIR}")
            sys.exit(1)
        mp3_files = sorted(DOWNLOADS_DIR.glob("*.mp3")) + \
                    sorted(DOWNLOADS_DIR.glob("*.m4a")) + \
                    sorted(DOWNLOADS_DIR.glob("*.wav"))
        if not mp3_files:
            print(f"❌ {DOWNLOADS_DIR} 目录中没有音频文件")
            sys.exit(1)

    # 跳过已有同名 txt 的文件（除非用户确认重新转录）
    pending = []
    skipped = []
    for f in mp3_files:
        txt = f.with_suffix(".txt")
        if txt.exists():
            skipped.append(f)
        else:
            pending.append(f)

    if skipped:
        print(f"\n⏭  以下文件已有转录结果，跳过（删除对应 .txt 可重新转录）：")
        for f in skipped:
            print(f"   {f.name}")

    if not pending:
        print("\n✅ 所有文件均已转录完成")
        return

    print(f"\n📋 待转录文件: {len(pending)} 个")
    for i, f in enumerate(pending, 1):
        size_mb = f.stat().st_size / 1024 / 1024
        print(f"   {i}. {f.name}  ({size_mb:.1f} MB)")

    # 3. 逐个转录
    success_count = 0
    for i, audio_path in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] 处理中...")
        text = transcribe_file(audio_path)
        if text is not None:
            txt_path = save_transcript(audio_path, text)
            success_count += 1
            # 预览前 200 字
            preview = text[:200].replace("\n", " ")
            print(f"\n📝 内容预览:\n   {preview}{'...' if len(text) > 200 else ''}")
        else:
            print(f"⚠️  跳过 {audio_path.name}")

    # 4. 汇总
    print(f"\n{'=' * 55}")
    print(f"🎉  完成！成功转录 {success_count}/{len(pending)} 个文件")
    if success_count > 0:
        print(f"📁  结果保存在: {DOWNLOADS_DIR.resolve()}/")
    print("=" * 55)


if __name__ == "__main__":
    main()
