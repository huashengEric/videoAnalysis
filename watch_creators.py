#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量关注多位抖音博主并自动处理最新视频
======================================

整体流程：
  1. 从项目根目录读取 creators.json，获取要关注的博主列表；
  2. 对每位博主，调用 fetch_creator_videos.py 中的逻辑获取最近视频列表；
  3. 结合本地去重记录 processed_aweme_ids.json，筛出“本次新增”的视频；
  4. 对每个新增视频，调用 main.run_pipeline(url) 跑完整流程（下载→转录→DeepSeek 分析）。

配置示例（creators.json）：

[
  {
    "name": "王五拾",
    "url": "https://www.douyin.com/user/MS4wLjABAAAAwgHmo70sCDE2i7QpFyFZR6uqbj9pYQ6__NIwj1dmd1Q?from_tab_name=main_videos",
    "max_new_videos": 3
  },
  {
    "name": "某茶饮老板",
    "url": "https://www.douyin.com/user/xxxxxxxxx",
    "max_new_videos": 2
  }
]

使用方法（在已激活虚拟环境中）：

    source .venv/bin/activate
    python watch_creators.py

注意：
  - creators.json 中的 url 建议直接粘贴你在浏览器中打开某个博主主页时的完整地址；
  - 首次运行会在 downloads/ 下创建 processed_aweme_ids.json 作为“已处理视频”去重记录。
"""

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Set

PROJECT_ROOT = Path(__file__).parent
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
CREATORS_FILE = PROJECT_ROOT / "creators.json"
PROCESSED_FILE = DOWNLOADS_DIR / "processed_aweme_ids.json"


@dataclass
class Creator:
    name: str
    url: str
    max_new_videos: int = 3
    # douyin | youtube（可扩展更多平台）
    platform: str = "douyin"


def load_creators() -> List[Creator]:
    if not CREATORS_FILE.exists():
        print("❌ 未找到 creators.json，请先在项目根目录创建，示例内容如下：\n")
        example = [
            {
                "name": "示例博主",
                "url": "https://www.douyin.com/user/xxxxxxxxx",
                "max_new_videos": 3,
                "platform": "douyin",
            }
        ]
        print(json.dumps(example, ensure_ascii=False, indent=2))
        sys.exit(1)

    raw = json.loads(CREATORS_FILE.read_text(encoding="utf-8"))
    creators: List[Creator] = []
    for item in raw:
        name = item.get("name") or "未知博主"
        url = item.get("url", "").strip()
        if not url:
            continue
        max_new = int(item.get("max_new_videos") or 3)
        plat = str(item.get("platform") or "douyin").lower().strip()
        if plat not in ("douyin", "youtube"):
            plat = "douyin"
        creators.append(Creator(name=name, url=url, max_new_videos=max_new, platform=plat))
    if not creators:
        print("❌ creators.json 中没有有效的博主配置")
        sys.exit(1)
    return creators


def load_processed_ids() -> Set[str]:
    if not PROCESSED_FILE.exists():
        return set()
    try:
        data = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(str(x) for x in data)
        elif isinstance(data, dict) and "ids" in data:
            return set(str(x) for x in data.get("ids", []))
    except Exception:
        return set()
    return set()


def save_processed_ids(ids: Set[str]) -> None:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    data = {"ids": sorted(ids)}
    PROCESSED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def fetch_videos_for_creator(creator: Creator) -> List[Dict[str, Any]]:
    from fetch_creator_videos import fetch_creator_videos

    print(f"\n{'=' * 60}")
    print(f"👤 博主：{creator.name}")
    print(f"🔗 主页：{creator.url}")
    print(f"{'-' * 60}")

    videos = await fetch_creator_videos(creator.url, max_videos=50)
    if not videos:
        print("⚠️ 未能获取到视频列表，跳过该博主。")
        return []

    print(f"📋 共获取到 {len(videos)} 条视频（按接口返回顺序）")
    return videos


async def process_new_videos(creators: List[Creator]) -> None:
    from main import run_pipeline  # 异步函数

    processed_ids = load_processed_ids()
    print(f"✅ 已载入已处理视频数：{len(processed_ids)}")

    total_new = 0

    for creator in creators:
        videos = await fetch_videos_for_creator(creator)
        if not videos:
            continue

        # 按 create_time 降序（最新在前），部分接口本身已是降序，这里再保险排一次
        videos_sorted = sorted(
            videos,
            key=lambda v: v.get("create_time") or 0,
            reverse=True,
        )

        new_videos: List[Dict[str, Any]] = []
        for v in videos_sorted:
            aweme_id = str(v.get("aweme_id"))
            if not aweme_id:
                continue
            if aweme_id in processed_ids:
                continue
            new_videos.append(v)
            if len(new_videos) >= creator.max_new_videos:
                break

        if not new_videos:
            print("✅ 没有发现新的视频（或都已处理过）")
            continue

        print(f"🆕 发现新的视频 {len(new_videos)} 条，将按顺序处理：\n")
        for i, v in enumerate(new_videos, 1):
            print(f"  {i:2d}. {v['title']}")
            print(f"      {v['video_url']}")

        # 依次跑完整流水线
        for v in new_videos:
            url = v["video_url"]
            aweme_id = str(v["aweme_id"])
            title = v.get("title", "")

            print(f"\n{'-' * 60}")
            print(f"▶ 开始处理：{title}")
            print(f"   链接：{url}")
            print(f"{'-' * 60}")

            try:
                await run_pipeline(url)
                processed_ids.add(aweme_id)
                total_new += 1
            except SystemExit as e:
                # main.run_pipeline 内部在严重错误时会调用 sys.exit
                print(f"❌ 处理该视频时退出（SystemExit: {e.code}），跳过。")
            except Exception as e:
                print(f"❌ 处理该视频出错：{e}")

            # 每处理完一个视频就立即保存去重记录，避免中途断电导致重复处理
            save_processed_ids(processed_ids)

    print(f"\n{'=' * 60}")
    print(f"🎉 本次共成功处理新视频：{total_new} 条")
    print(f"📁 已处理视频 ID 记录文件：{PROCESSED_FILE}")
    print(f"{'=' * 60}")


def main() -> None:
    print("=" * 60)
    print("  👁  多博主新视频监控 & 自动分析")
    print("=" * 60)

    creators = load_creators()
    print(f"📌 已配置博主数量：{len(creators)}")

    asyncio.run(process_new_videos(creators))


if __name__ == "__main__":
    main()

