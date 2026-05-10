#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 频道最近视频（公开 RSS，无需 API Key）
============================================
通过 https://www.youtube.com/feeds/videos.xml?channel_id=UC... 获取条目。

频道链接支持：
  - https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx
  - https://www.youtube.com/@handle  （解析页面中的 channelId）
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# YouTube 对「仅 UA」的 RSS 请求常返回 404；补全浏览器头可显著提高成功率
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}

ATOM_NS = "http://www.w3.org/2005/Atom"
# 官方 Feed 当前使用 2015；旧文档里的 v2015 仍兼容查找
YT_NS = "http://www.youtube.com/xml/schemas/2015"
YT_NS_LEGACY = "http://www.youtube.com/xml/schemas/v2015"


async def resolve_youtube_channel_id(url: str) -> str | None:
    """从频道主页 URL 解析出 channel_id（UC 开头）。"""
    u = (url or "").strip()
    if not u:
        return None

    m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", u, re.I)
    if m:
        return m.group(1)

    if not u.startswith("http"):
        u = "https://www.youtube.com/" + u.lstrip("/")

    # /@handle 或 /c/xxx 等：拉取页面 HTML 找 channelId
    page_headers = {
        **BROWSER_HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=45.0) as client:
        r = await client.get(u, headers=page_headers)
        r.raise_for_status()
        text = r.text

    for pat in (
        r'"channelId":"(UC[\w-]{22})"',
        r'"externalId":"(UC[\w-]{22})"',
        r'"browseId":"(UC[\w-]{22})"',
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
        r"channel_id=(UC[\w-]{22})",
    ):
        m2 = re.search(pat, text)
        if m2:
            return m2.group(1)
    return None


def _parse_rss_entries(xml_text: str, max_videos: int) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    out: list[dict[str, Any]] = []

    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        vid_el = entry.find(f"{{{YT_NS}}}videoId")
        if vid_el is None:
            vid_el = entry.find(f"{{{YT_NS_LEGACY}}}videoId")
        if vid_el is None or not (vid_el.text or "").strip():
            continue
        video_id = vid_el.text.strip()

        title_el = entry.find(f"{{{ATOM_NS}}}title")
        title = (title_el.text or "").strip() if title_el is not None else ""

        create_time = 0
        published_el = entry.find(f"{{{ATOM_NS}}}published")
        if published_el is not None and (published_el.text or "").strip():
            try:
                raw = published_el.text.strip()
                if raw.endswith("Z"):
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                create_time = int(dt.timestamp())
            except Exception:
                pass

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        out.append(
            {
                "aweme_id": video_id,
                "title": title,
                "create_time": create_time,
                "is_top": False,
                "video_url": video_url,
                "stats": {
                    "digg_count": 0,
                    "comment_count": 0,
                    "share_count": 0,
                    "collect_count": 0,
                },
                "platform": "youtube",
                "source_platform": "youtube",
            }
        )
        if len(out) >= max_videos:
            break

    return out


async def _fetch_rss_xml(client: httpx.AsyncClient, feed_url: str) -> str | None:
    """成功返回 XML 文本；持续 404 返回 None（换 playlist RSS 或 yt-dlp）；其它 HTTP 错误仍抛出。"""
    for attempt in range(2):
        try:
            r = await client.get(feed_url, headers=BROWSER_HEADERS)
            if r.status_code == 404:
                if attempt == 0:
                    await asyncio.sleep(0.6)
                    continue
                return None
            r.raise_for_status()
            return r.text
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            if code == 404:
                if attempt == 0:
                    await asyncio.sleep(0.6)
                    continue
                return None
            raise
    return None


def _uploads_playlist_id(channel_id: str) -> str | None:
    """频道「上传」列表：UCxxxx → UU + xxxx 后半（与官方 RSS 等价源）。"""
    if not channel_id.startswith("UC") or len(channel_id) < 4:
        return None
    return "UU" + channel_id[2:]


def _ytdlp_list_videos_sync(channel_url: str, max_videos: int) -> list[dict[str, Any]]:
    """
    RSS 不可用时的后备：yt-dlp 拉取频道 /videos 扁平列表（与下载分析共用依赖）。
    """
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "-j",
        "--flat-playlist",
        "--playlist-end",
        str(max_videos),
        "--no-warnings",
        "--no-playlist-reverse",
        channel_url.strip(),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("yt-dlp 拉取列表超时（>120s）") from e

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:800]
        raise RuntimeError(f"yt-dlp 拉取失败：{err or f'exit {proc.returncode}'}")

    out: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("_type") != "url":
            continue
        video_id = str(obj.get("id") or "").strip()
        if not video_id:
            continue
        title = str(obj.get("title") or "").strip()
        ts = obj.get("timestamp")
        create_time = int(ts) if isinstance(ts, (int, float)) else 0

        out.append(
            {
                "aweme_id": video_id,
                "title": title,
                "create_time": create_time,
                "is_top": False,
                "video_url": f"https://www.youtube.com/watch?v={video_id}",
                "stats": {
                    "digg_count": 0,
                    "comment_count": 0,
                    "share_count": 0,
                    "collect_count": 0,
                },
                "platform": "youtube",
                "source_platform": "youtube",
            }
        )
        if len(out) >= max_videos:
            break

    return out


async def fetch_youtube_videos(channel_url: str, max_videos: int = 10) -> list[dict[str, Any]]:
    """
    返回与抖音侧尽量一致的字典列表（aweme_id 字段存放 YouTube video_id）。

    优先 Atom RSS（快）；若 YouTube 返回 404 或空，则回退 yt-dlp（更稳，与视频下载同源）。
    """
    url = (channel_url or "").strip()
    if not url:
        raise RuntimeError("频道链接为空")

    cid = await resolve_youtube_channel_id(url)
    if not cid:
        # 仍尝试直接用 yt-dlp（部分 URL 可不解析 UC）
        try:
            return await asyncio.to_thread(_ytdlp_list_videos_sync, url, max_videos)
        except Exception as e:
            raise RuntimeError(
                "无法解析 YouTube 频道 ID，且 yt-dlp 拉取也失败。请使用：\n"
                "  https://www.youtube.com/@YourHandle\n"
                "  或 https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx\n"
                f"详情：{e}"
            ) from e

    xml_text: str | None = None
    async with httpx.AsyncClient(follow_redirects=True, timeout=45.0) as client:
        feed_channel = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
        xml_text = await _fetch_rss_xml(client, feed_channel)

        if xml_text is None and cid.startswith("UC"):
            pid = _uploads_playlist_id(cid)
            if pid:
                feed_pl = f"https://www.youtube.com/feeds/videos.xml?playlist_id={pid}"
                xml_text = await _fetch_rss_xml(client, feed_pl)

    if xml_text:
        try:
            parsed = _parse_rss_entries(xml_text, max_videos)
            if parsed:
                return parsed
        except ET.ParseError:
            pass

    # RSS 失败或解析无条目：yt-dlp
    try:
        return await asyncio.to_thread(_ytdlp_list_videos_sync, url, max_videos)
    except Exception as e:
        raise RuntimeError(
            f"YouTube 列表获取失败（RSS 不可用且 yt-dlp 失败）。请确认已安装依赖：pip install -r requirements.txt；"
            f"并检查网络能否访问 YouTube。详情：{e}"
        ) from e
