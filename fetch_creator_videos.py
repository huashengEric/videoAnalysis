#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量获取抖音博主最新视频链接
============================

思路：
  - 复用现有的 Playwright + Cookie 能力；
  - 打开博主主页（例如 https://www.douyin.com/user/xxxxx 或类似链接）；
  - 监听 /aweme/v1/web/aweme/post/ 接口响应，从 JSON 里提取 aweme_list；
  - 把每条 aweme 的 aweme_id 转成 https://www.douyin.com/video/<aweme_id> 形式输出。

使用方法（在已激活的虚拟环境中）：

    # 交互式输入博主主页链接
    python fetch_creator_videos.py

    # 或命令行直接指定
    python fetch_creator_videos.py "https://www.douyin.com/user/xxx"

注意：
  - 依赖：playwright、httpx、已经存在的 Cookie 文件（与 douyin_audio_downloader.py 相同）
  - 你需要先在浏览器登录抖音，并用 Cookie-Editor 之类的插件导出 www.douyin.com 的 Cookie 到
    www.douyin.com_cookies.json（本项目已经在用）
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import List, Dict, Any

from playwright.async_api import async_playwright, BrowserContext

# 复用音频下载器里的 Cookie 加载逻辑和 UA 配置
from douyin_audio_downloader import load_cookies, USER_AGENT


DOWNLOADS_DIR = Path(__file__).parent / "downloads"


async def fetch_creator_videos(creator_url: str, max_videos: int = 10) -> List[Dict[str, Any]]:
    """
    打开博主主页，监听 aweme post 接口，抓取该页面返回的 aweme 列表。

    :param creator_url: 抖音博主主页地址（例如 https://www.douyin.com/user/xxx 或其他形式）
    :param max_videos:  最多返回的视频条数
    :return:            每条视频的基础信息列表，包括 aweme_id、title、create_time、video_url
    """
    cookies = load_cookies()

    results: List[Dict[str, Any]] = []

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
        # 简单规避基础反爬特征
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )

        if cookies:
            await context.add_cookies(cookies)

        page = await context.new_page()

        done_event = asyncio.Event()

        async def on_response(response):
            nonlocal results
            if results:  # 已经拿到数据就不再处理
                return
            url = response.url
            # 仅关注 aweme post 列表接口
            if "/aweme/v1/web/aweme/post/" not in url:
                return
            if response.status != 200:
                return
            try:
                body = await response.json()
                aweme_list = body.get("aweme_list") or []
                items: List[Dict[str, Any]] = []
                for aweme in aweme_list:
                    aweme_id = aweme.get("aweme_id")
                    desc = aweme.get("desc", "")
                    create_time = aweme.get("create_time")
                    is_top = bool(aweme.get("is_top") or aweme.get("is_top_item") or aweme.get("is_pinned"))
                    if not aweme_id:
                        continue

                    # 互动统计数据（点赞、评论、分享、收藏等）
                    statistics = aweme.get("statistics") or {}
                    digg_count = statistics.get("digg_count", 0)
                    comment_count = statistics.get("comment_count", 0)
                    share_count = statistics.get("share_count", 0)
                    collect_count = statistics.get("collect_count", 0)

                    video_url = f"https://www.douyin.com/video/{aweme_id}"
                    items.append(
                        {
                            "aweme_id": aweme_id,
                            "title": desc,
                            "create_time": create_time,
                            "is_top": is_top,
                            "video_url": video_url,
                            "stats": {
                                "digg_count": digg_count,
                                "comment_count": comment_count,
                                "share_count": share_count,
                                "collect_count": collect_count,
                                "raw": statistics,
                            },
                        }
                    )
                if items:
                    # 把最新的视频排在前面（通常接口本身已是按时间倒序）
                    results = items[:max_videos]
                    done_event.set()
            except Exception:
                # 忽略解析错误，等待下一个响应
                return

        page.on("response", on_response)

        print(f"🌐 正在打开博主主页：{creator_url}")
        try:
            await page.goto(creator_url, wait_until="domcontentloaded", timeout=30_000)
            print("📄 页面 DOM 已加载，等待视频列表 API...")

            # 一些创作者主页需要滚动一下才会触发接口请求，这里简单向下滚动几次
            for _ in range(3):
                await page.mouse.wheel(0, 2000)
                try:
                    await asyncio.wait_for(done_event.wait(), timeout=3)
                    break
                except asyncio.TimeoutError:
                    continue

            # 如果上面的滚动没触发接口，再等一会儿
            if not results:
                try:
                    await asyncio.wait_for(done_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

        finally:
            await browser.close()

    return results


def save_creator_videos(creator_url: str, videos: List[Dict[str, Any]]) -> Path:
    """
    把抓到的视频列表保存为 JSON 文件，方便后续按需复用。
    """
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = creator_url.replace("https://", "").replace("http://", "").replace("/", "_")
    out_path = DOWNLOADS_DIR / f"{safe_name}_videos.json"
    out_path.write_text(json.dumps(videos, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    import textwrap

    print("=" * 60)
    print("  👤  抖音博主最新视频抓取 (Playwright)")
    print("=" * 60)

    if len(sys.argv) > 1:
        creator_url = sys.argv[1].strip()
    else:
        print(
            textwrap.dedent(
                """
                请输入抖音博主主页链接，例如：
                  - https://www.douyin.com/user/xxxxxx
                  - 或其它能打开该博主主页的视频列表页面链接
                """
            ).strip()
        )
        creator_url = input("\n博主主页链接：\n> ").strip()

    if not creator_url:
        print("❌ 未输入链接，退出")
        sys.exit(1)

    max_videos = 10

    videos: List[Dict[str, Any]] = asyncio.run(fetch_creator_videos(creator_url, max_videos=max_videos))
    if not videos:
        print("❌ 未能获取到该博主的视频列表，请检查：")
        print("   1）链接是否是博主主页或包含视频列表的页面；")
        print("   2）Cookie 是否有效（重新从浏览器导出 www.douyin.com 的 Cookie）；")
        print("   3）如为企业号/特殊账号，页面结构可能不同，需要单独适配。")
        sys.exit(1)

    print(f"\n📋 获取到该博主最近 {len(videos)} 条视频：\n")
    for i, v in enumerate(videos, 1):
        print(f"{i:2d}. {v['title']}")
        stats = (v.get("stats") or {})
        digg = stats.get("digg_count", 0)
        comments = stats.get("comment_count", 0)
        shares = stats.get("share_count", 0)
        collects = stats.get("collect_count", 0)
        # 简要显示互动数据，方便人工浏览
        print(f"    {v['video_url']}")
        print(f"    👍 {digg}  💬 {comments}  🔁 {shares}  ⭐ {collects}")

    out_path = save_creator_videos(creator_url, videos)
    print(f"\n💾 已保存到：{out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()

