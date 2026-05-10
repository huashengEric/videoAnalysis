#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音视频音频下载器
================
使用 Playwright 模拟真实浏览器，拦截 /aweme/v1/web/aweme/detail/ API 响应，
从 JSON 数据中提取音乐直链（.mp3）或视频链接，实现无需转码的音频下载。

快速开始：
    pip install -r requirements.txt
    playwright install chromium
    python douyin_audio_downloader.py <抖音视频链接>

系统依赖（仅当需要从视频提取音频时）：
    macOS:   brew install ffmpeg
    Ubuntu:  sudo apt install ffmpeg
"""

import asyncio
import json
import re
import sys
import subprocess
import time
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, BrowserContext

# ──────────────────────────────────────────────
# 全局配置
# ──────────────────────────────────────────────

# 下载文件保存目录
DOWNLOAD_DIR = Path("downloads")

# Cookie 文件（与脚本同目录，优先 JSON 格式）
COOKIE_FILE_JSON = Path(__file__).parent / "www.douyin.com_cookies.json"
COOKIE_FILE_TXT  = Path(__file__).parent / "www.douyin.com_cookies.txt"

# HTTP 请求超时（秒）
REQUEST_TIMEOUT = 90

# 等待 API 响应的最长时间（秒）
API_WAIT_SECONDS = 15

# 模拟 Chrome 浏览器的 User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def safe_filename(name: str, max_len: int = 60) -> str:
    """清除文件名非法字符并截断"""
    name = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", name).strip(" .")
    return name[:max_len] if name else "douyin_video"


# ──────────────────────────────────────────────
# Cookie 加载
# ──────────────────────────────────────────────

def load_cookies() -> list[dict]:
    """
    加载 Cookie，优先使用 JSON 格式（浏览器扩展 Cookie-Editor 导出）。
    回退到 Netscape .txt 格式（yt-dlp 使用的格式）。
    """
    # ── JSON 格式 ──
    if COOKIE_FILE_JSON.exists():
        try:
            raw = json.loads(COOKIE_FILE_JSON.read_text(encoding="utf-8"))
            cookies = []
            for c in raw:
                name = c.get("name", "").strip()
                if not name:
                    continue
                cookies.append({
                    "name":     name,
                    "value":    c.get("value", ""),
                    "domain":   c.get("domain", ".douyin.com"),
                    "path":     c.get("path", "/"),
                    "secure":   c.get("secure", False),
                    "httpOnly": c.get("httpOnly", False),
                })
            print(f"✅ 已从 JSON 加载 {len(cookies)} 个 Cookie")
            return cookies
        except Exception as e:
            print(f"⚠️  JSON Cookie 解析失败: {e}")

    # ── Netscape TXT 格式 ──
    if COOKIE_FILE_TXT.exists():
        try:
            cookies = []
            for line in COOKIE_FILE_TXT.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _, path, secure, _exp, name, value = parts[:7]
                if not name:
                    continue
                cookies.append({
                    "name":     name,
                    "value":    value,
                    "domain":   domain,
                    "path":     path,
                    "secure":   secure.upper() == "TRUE",
                    "httpOnly": False,
                })
            print(f"✅ 已从 TXT 加载 {len(cookies)} 个 Cookie")
            return cookies
        except Exception as e:
            print(f"⚠️  TXT Cookie 解析失败: {e}")

    print("⚠️  未找到 Cookie 文件，以匿名模式访问（部分视频可能无法下载）")
    return []


# ──────────────────────────────────────────────
# 核心：通过浏览器拦截 API 获取视频数据
# ──────────────────────────────────────────────

async def fetch_aweme_data(douyin_url: str, cookies: list[dict]) -> dict:
    """
    启动无头浏览器，打开抖音视频页面，拦截
    /aweme/v1/web/aweme/detail/ API 的 JSON 响应。

    该 API 返回的 JSON 包含：
    - aweme_list[0].desc           视频标题
    - aweme_list[0].music.play_url.url_list  背景音乐 MP3 直链列表
    - aweme_list[0].video.play_addr.url_list 视频 MP4 链接列表

    :param douyin_url: 抖音视频链接
    :param cookies:    已加载的 Cookie 列表
    :return:           aweme_list[0] 字典，未找到时返回 {}
    """
    result: dict = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--mute-audio",
            ],
        )
        context: BrowserContext = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        # 隐藏 WebDriver 特征，规避基础反爬检测
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        # 注入已登录的 Cookie
        if cookies:
            await context.add_cookies(cookies)
            print("🍪 Cookie 已注入浏览器")

        page = await context.new_page()

        # ── 异步事件：监听所有响应，找到视频 detail API ──
        found_event = asyncio.Event()

        async def on_response(response):
            """拦截包含 aweme_list 的 JSON 响应"""
            if result:           # 已找到，不再处理
                return
            url = response.url
            # 只处理 aweme detail 接口
            if "/aweme/v1/web/aweme/detail/" not in url:
                return
            if response.status != 200:
                return
            try:
                body = await response.json()
                # 兼容两种响应格式：
                #   新版：aweme_detail（单条详情）
                #   旧版：aweme_list（列表，取第一条）
                aweme = (
                    body.get("aweme_detail")
                    or (body.get("aweme_list") or [None])[0]
                )
                if aweme:
                    result.update(aweme)
                    print(f"✅ 已获取视频数据（API: {url[:80]}...）")
                    found_event.set()
            except Exception:
                pass

        page.on("response", on_response)

        print(f"🌐 正在打开: {douyin_url}")
        try:
            await page.goto(douyin_url, wait_until="domcontentloaded", timeout=30_000)
            print("📄 页面 DOM 已加载，等待视频详情 API...")

            # 等待 API 响应，超时后继续
            try:
                await asyncio.wait_for(found_event.wait(), timeout=API_WAIT_SECONDS)
            except asyncio.TimeoutError:
                print("⏱  等待超时，检查是否已有数据...")

        except Exception as e:
            print(f"⚠️  页面加载异常: {e}")
        finally:
            await browser.close()

    return result


def extract_urls_from_aweme(aweme: dict) -> dict:
    """
    从 aweme 数据中提取标题、音乐直链、视频链接。

    返回结构：
    {
        "title":       str,   视频标题
        "music_urls":  list,  音乐 MP3 直链列表（可直接下载）
        "video_urls":  list,  视频 MP4 链接列表
        "music_name":  str,   音乐名称
    }
    """
    title = safe_filename(aweme.get("desc", "douyin_video"))

    # 背景音乐（已是 MP3）
    music = aweme.get("music", {})
    music_name = music.get("title", "")
    play_url = music.get("play_url", {})
    music_urls = play_url.get("url_list", [])
    if not music_urls and play_url.get("uri", "").startswith("http"):
        music_urls = [play_url["uri"]]

    # 视频地址（多个 CDN 备用）
    video = aweme.get("video", {})
    play_addr = video.get("play_addr", {})
    video_urls = play_addr.get("url_list", [])

    return {
        "title":      title,
        "music_name": music_name,
        "music_urls": music_urls,
        "video_urls": video_urls,
    }


# ──────────────────────────────────────────────
# 文件下载
# ──────────────────────────────────────────────

async def download_file(urls: list[str], save_path: Path, label: str = "文件") -> bool:
    """
    依次尝试 URL 列表中的地址，直到下载成功。
    使用流式下载并显示进度条。

    :param urls:      候选 URL 列表（按优先级排列）
    :param save_path: 本地保存路径
    :param label:     显示用的名称（"音频"/"视频"）
    :return:          是否下载成功
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Referer":    "https://www.douyin.com/",
        "Accept":     "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    BAR = 35  # 进度条宽度

    for i, url in enumerate(urls, 1):
        print(f"📥 下载{label}（尝试 {i}/{len(urls)}）：{url[:80]}...")
        try:
            async with httpx.AsyncClient(
                headers=headers,
                follow_redirects=True,
                timeout=REQUEST_TIMEOUT,
            ) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code not in (200, 206):
                        print(f"   HTTP {resp.status_code}，换下一个地址")
                        continue

                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0

                    with open(save_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65_536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            # 进度条
                            if total > 0:
                                pct    = downloaded / total
                                filled = int(BAR * pct)
                                bar    = "█" * filled + "░" * (BAR - filled)
                                print(
                                    f"\r  [{bar}] {pct*100:5.1f}%  "
                                    f"{downloaded//1024:,}KB / {total//1024:,}KB",
                                    end="", flush=True,
                                )
                            else:
                                print(f"\r  已下载: {downloaded//1024:,} KB", end="", flush=True)

                    size_kb = save_path.stat().st_size // 1024
                    print(f"\n✅ {label}下载完成: {save_path.name}  ({size_kb:,} KB)")
                    return True

        except httpx.TimeoutException:
            print(f"\n   超时，换下一个地址")
        except Exception as e:
            print(f"\n   出错: {e}")

    print(f"❌ 所有地址均下载失败")
    return False


# ──────────────────────────────────────────────
# 音频提取（从视频中提取，备用方案）
# ──────────────────────────────────────────────

def extract_audio_from_video(video_path: Path, audio_path: Path) -> bool:
    """
    使用 ffmpeg 从视频文件中提取音频并保存为 MP3。
    仅在无法直接获取音乐直链时使用。

    ffmpeg 参数：
      -vn       不处理视频流
      -ar 44100 采样率 44.1 kHz
      -ac 2     立体声
      -b:a 192k 比特率 192 kbps
    """
    print("🎵 正在从视频提取音频（ffmpeg）...")
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(video_path),
             "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", "-y",
             str(audio_path)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            size_kb = audio_path.stat().st_size // 1024
            print(f"✅ 音频提取完成: {audio_path.name}  ({size_kb:,} KB)")
            return True
        print("❌ ffmpeg 执行失败：")
        for line in r.stderr.strip().splitlines()[-5:]:
            print(f"   {line}")
        return False
    except FileNotFoundError:
        print("❌ 未找到 ffmpeg，请先安装：brew install ffmpeg")
        return False


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

async def main() -> None:
    print("=" * 62)
    print("  🎵  抖音视频音频下载器  (Playwright + API 拦截)")
    print("=" * 62)

    # 1. 获取视频链接
    if len(sys.argv) > 1:
        douyin_url = sys.argv[1].strip()
    else:
        douyin_url = input("\n请输入抖音视频链接: ").strip()

    if not douyin_url:
        print("❌ 未输入链接，退出")
        sys.exit(1)

    # 2. 加载 Cookie
    print("\n📂 加载 Cookie...")
    cookies = load_cookies()

    # 3. 准备下载目录
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # 4. 启动浏览器，拦截 API 获取视频元数据
    print("\n🔍 启动浏览器，获取视频信息...")
    aweme = await fetch_aweme_data(douyin_url, cookies)

    if not aweme:
        print("\n❌ 未能获取视频数据，请检查：")
        print("   1. 链接是否正确（支持完整链接或 v.douyin.com 短链）")
        print("   2. Cookie 是否已过期（重新从浏览器导出）")
        print("   3. 视频是否设置了仅好友可见等权限")
        sys.exit(1)

    # 5. 提取 URL 信息
    urls = extract_urls_from_aweme(aweme)
    title      = urls["title"]
    music_name = urls["music_name"]
    music_urls = urls["music_urls"]
    video_urls = urls["video_urls"]

    print(f"\n📋 视频标题: {title}")
    print(f"🎵 音乐名称: {music_name}")
    print(f"   音乐链接数: {len(music_urls)}")
    print(f"   视频链接数: {len(video_urls)}")

    ts = int(time.time())

    # 6. 优先下载音乐直链（已是 MP3，无需 ffmpeg）
    if music_urls:
        audio_path = DOWNLOAD_DIR / f"{title}_{ts}.mp3"
        print(f"\n🎵 直接下载音乐 MP3（无需转码）...")
        ok = await download_file(music_urls, audio_path, label="音频")
        if ok:
            print(f"\n{'=' * 62}")
            print(f"🎉  完成！音频已保存至：")
            print(f"    {audio_path.resolve()}")
            print(f"{'=' * 62}")
            return

    # 7. 备用：下载视频后用 ffmpeg 提取音频
    if video_urls:
        print("\n⚠️  音乐直链下载失败，改为从视频提取音频...")

        # 检查 ffmpeg
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print("❌ 未找到 ffmpeg，请先安装：brew install ffmpeg")
            sys.exit(1)

        video_path = DOWNLOAD_DIR / f"{title}_{ts}_tmp.mp4"
        audio_path = DOWNLOAD_DIR / f"{title}_{ts}.mp3"

        ok = await download_file(video_urls, video_path, label="视频")
        if not ok:
            print("❌ 视频下载也失败了，退出")
            sys.exit(1)

        if extract_audio_from_video(video_path, audio_path):
            video_path.unlink(missing_ok=True)
            print(f"\n{'=' * 62}")
            print(f"🎉  完成！音频已保存至：")
            print(f"    {audio_path.resolve()}")
            print(f"{'=' * 62}")
        else:
            print(f"⚠️  音频提取失败，视频保留在: {video_path}")
    else:
        print("❌ 未找到可用的下载链接")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
