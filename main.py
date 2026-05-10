#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音视频一键处理管道
====================
整合三个步骤，输入一个抖音链接，自动完成：

  第一步 🎵  下载音频    douyin_audio_downloader.py
  第二步 🎙  转录文字    transcribe_audio.py  (需要 AssemblyAI Key)
  第三步 🤖  DeepSeek 分析 deepseek_analyze.py (需要 DeepSeek API Key)

用法：
    python main.py
    python main.py https://www.douyin.com/video/xxxx

.env 文件配置：
    ASSEMBLYAI_API_KEY="your_key"
    DEEPSEEK_API_KEY="your_key"

输出（均在 downloads/ 目录）：
    {标题}_{aweme_id}_{ts}.mp3           原始音频
    {标题}_{aweme_id}_{ts}.txt           转录文字
    {标题}_{aweme_id}_{ts}_deepseek.txt  DeepSeek 分析报告
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

DOWNLOADS_DIR = Path(__file__).parent / "downloads"
DOTENV_FILE   = Path(__file__).parent / ".env"
SEP  = "═" * 56
SEP2 = "─" * 56

def load_dotenv():
    """把 .env 文件里的 KEY=VALUE 载入 os.environ"""
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

def snapshot_dir(suffix: str) -> set[Path]:
    """返回 downloads/ 目录中指定后缀的文件集合（用于前后对比）"""
    if not DOWNLOADS_DIR.exists():
        return set()
    return set(DOWNLOADS_DIR.glob(f"*{suffix}"))

def new_files(before: set[Path], after: set[Path]) -> list[Path]:
    """返回两次快照之间新增的文件，按修改时间排序"""
    added = after - before
    return sorted(added, key=lambda p: p.stat().st_mtime)

def step_banner(n: int, title: str, icon: str):
    print(f"\n{SEP}")
    print(f"  {icon}  步骤 {n}/3 · {title}")
    print(SEP)


# ──────────────────────────────────────────────
# 步骤 1：下载音频
# ──────────────────────────────────────────────

async def step_download(url: str) -> Path | None:
    """调用 douyin_audio_downloader 的核心函数下载音频"""
    from douyin_audio_downloader import load_cookies, fetch_aweme_data, \
        extract_urls_from_aweme, download_file

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    before = snapshot_dir(".mp3")

    cookies = load_cookies()
    print("🔍 正在获取视频信息...")
    aweme = await fetch_aweme_data(url, cookies)

    if not aweme:
        print("❌ 无法获取视频元数据，请检查链接或 Cookie 是否过期")
        return None

    urls_info = extract_urls_from_aweme(aweme)
    title      = urls_info["title"]
    music_urls = urls_info["music_urls"]
    video_urls = urls_info["video_urls"]
    aweme_id   = str(aweme.get("aweme_id", "") or "")

    print(f"📋 视频标题: {title}")
    if aweme_id:
        print(f"🔢 aweme_id: {aweme_id}")

    ts         = int(time.time())
    suffix     = f"_{aweme_id}" if aweme_id else ""
    audio_path = DOWNLOADS_DIR / f"{title}{suffix}_{ts}.mp3"

    # 重要：优先取“视频本体”的音轨（与原视频一致），避免下载到背景配乐导致转写不符
    ok = False
    if video_urls:
        print("📹 下载视频并提取音频（与原视频一致）...")
        import subprocess

        video_path = DOWNLOADS_DIR / f"{title}{suffix}_{ts}_tmp.mp4"
        ok = await download_file(video_urls, video_path, label="视频")
        if ok:
            try:
                r = subprocess.run(
                    ["ffmpeg", "-i", str(video_path),
                     "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", "-y",
                     str(audio_path)],
                    capture_output=True,
                )
                ok = r.returncode == 0
                if not ok:
                    print("❌ ffmpeg 提取音频失败，将尝试使用背景配乐直链（可能与原视频不一致）")
            except FileNotFoundError:
                ok = False
                print("⚠️ 未找到 ffmpeg，无法从视频提取音频，将尝试使用背景配乐直链（可能与原视频不一致）")
        video_path.unlink(missing_ok=True)

    if (not ok) and music_urls:
        print("🎵 下载背景配乐直链（MP3，可能与原视频人声不一致）...")
        ok = await download_file(music_urls, audio_path, label="音频")

    if not video_urls and not music_urls:
        print("❌ 未找到可下载的音频/视频链接")
        return None

    if not ok:
        return None

    after = snapshot_dir(".mp3")
    new   = new_files(before, after)
    return new[0] if new else audio_path


def is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def _step_download_youtube_sync(video_url: str) -> Path | None:
    """使用 yt-dlp 下载音频为 MP3（需本机已安装 ffmpeg）。"""
    import shutil
    import subprocess

    if not shutil.which("yt-dlp"):
        print("❌ 未找到 yt-dlp，请执行: pip install yt-dlp")
        return None
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    before = snapshot_dir(".mp3")
    ts = int(time.time())
    template = str(DOWNLOADS_DIR / f"%(title).100s_%(id)s_{ts}.%(ext)s")
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format",
        "mp3",
        "--no-playlist",
        "-o",
        template,
        video_url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        print("❌ 无法执行 yt-dlp")
        return None
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[-2500:]
        print("❌ yt-dlp 失败:\n", err)
        return None
    after = snapshot_dir(".mp3")
    new = new_files(before, after)
    return new[0] if new else None


async def step_download_youtube(video_url: str) -> Path | None:
    import asyncio

    return await asyncio.to_thread(_step_download_youtube_sync, video_url)


# ──────────────────────────────────────────────
# 步骤 2：语音转录
# ──────────────────────────────────────────────

def step_transcribe(mp3_path: Path) -> Path | None:
    """调用 transcribe_audio 转录单个 MP3（本地 ASR）"""
    from transcribe_audio import transcribe_file, save_transcript

    before = snapshot_dir(".txt")
    text = transcribe_file(mp3_path)
    if text is None:
        return None

    txt_path = save_transcript(mp3_path, text)
    return txt_path


# ──────────────────────────────────────────────
# 步骤 3：Gemini 内容分析
# ──────────────────────────────────────────────

def step_analyze(txt_path: Path, analysis_mode: str = "douyin") -> Path | None:
    """调用 deepseek_analyze 对转录文字生成 AI 分析报告（douyin / youtube 两套提示词）。"""
    from deepseek_analyze import analyze_file, get_api_key

    api_key = get_api_key()
    return analyze_file(txt_path, api_key=api_key, analysis_mode=analysis_mode)


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────

def _normalize_analysis_mode(url: str, analysis_mode: str | None) -> str:
    """douyin | youtube。YouTube 链接固定走 YouTube 分析模板；其余默认抖音模板。"""
    if is_youtube_url(url):
        return "youtube"
    if analysis_mode:
        m = analysis_mode.lower().strip()
        if m in ("douyin", "youtube"):
            return m
    return "douyin"


async def run_pipeline(url: str, analysis_mode: str | None = None):
    total_start = time.time()
    mode = _normalize_analysis_mode(url, analysis_mode)

    # ── 步骤 1：下载 ──
    is_yt = is_youtube_url(url)
    step_banner(1, "下载 YouTube 音频" if is_yt else "下载抖音音频", "🎵")
    t0       = time.time()
    mp3_path = await step_download_youtube(url) if is_yt else await step_download(url)
    if not mp3_path:
        print("\n❌ 下载失败，流程终止")
        sys.exit(1)
    print(f"\n✅ 音频已保存：{mp3_path.name}  ({mp3_path.stat().st_size//1024:,} KB)"
          f"  [{time.time()-t0:.1f}s]")

    # ── 步骤 2：转录 ──
    step_banner(2, "语音转文字（本地 ASR）", "🎙")
    t0       = time.time()
    txt_path = step_transcribe(mp3_path)
    if not txt_path:
        print("\n❌ 转录失败，流程终止")
        sys.exit(1)
    print(f"\n✅ 转录完成：{txt_path.name}  [{time.time()-t0:.1f}s]")

    # ── 步骤 3：分析 ──
    step_banner(
        3,
        "内容分析（YouTube 模式）" if mode == "youtube" else "内容分析（抖音模式）",
        "📊",
    )
    t0          = time.time()
    report_path = step_analyze(txt_path, analysis_mode=mode)
    if not report_path:
        print("\n❌ 分析失败")
        sys.exit(1)
    print(f"\n✅ 分析完成：{report_path.name}  [{time.time()-t0:.1f}s]")

    # ── 汇总 ──
    total = time.time() - total_start
    print(f"\n{SEP}")
    print(f"  🎉  全部完成！总耗时 {total:.0f} 秒")
    print(SEP)
    print(f"  📁  输出目录：{DOWNLOADS_DIR.resolve()}/")
    print(SEP2)
    print(f"  🎵  音频：  {mp3_path.name}")
    print(f"  📝  转录：  {txt_path.name}")
    print(f"  🤖  DeepSeek 分析：{report_path.name}")
    print(SEP)


def main():
    load_dotenv()

    print(SEP)
    print("  🚀  抖音视频一键处理  下载→转录→分析")
    print(SEP)
    print("  依赖：ffmpeg · AssemblyAI Key · DeepSeek Key")
    print(SEP)

    # 获取链接
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
    else:
        url = input("\n请输入抖音视频链接（支持短链/完整链接）：\n> ").strip()

    if not url:
        print("❌ 未输入链接")
        sys.exit(1)

    # 检查 ffmpeg
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=3)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("❌ 未检测到 ffmpeg，请先安装：brew install ffmpeg")
        sys.exit(1)

    # 检查 AssemblyAI Key
    if not os.environ.get("ASSEMBLYAI_API_KEY"):
        print("⚠️  未检测到 ASSEMBLYAI_API_KEY，步骤 2 将提示输入")

    asyncio.run(run_pipeline(url))


if __name__ == "__main__":
    main()
