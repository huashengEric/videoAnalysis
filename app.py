#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音多博主内容分析 · Web 控制台（FastAPI）
========================================

功能目标：
  - 通过浏览器（电脑 / 手机）管理 creators.json 里的博主；
  - 查看每个博主最近的视频列表及基础数据（标题 + 点赞/评论/分享/收藏）；
  - 一键对某条视频触发「下载 → 转录 → DeepSeek 分析」流水线。

运行方式：
  1）安装依赖：pip install -r requirements.txt
  2）启动服务：
        uvicorn app:app --reload
  3）浏览器打开：
        http://127.0.0.1:8000/
"""

from __future__ import annotations

import asyncio
import json
import glob
import traceback
import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query
from openai import APIError, AuthenticationError, RateLimitError
from pydantic import BaseModel, AnyHttpUrl, field_validator
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from watch_creators import load_creators
from fetch_creator_videos import fetch_creator_videos
from fetch_youtube_videos import fetch_youtube_videos
from main import run_pipeline
from scheduler_store import (
    init_db,
    list_schedules,
    create_schedule,
    update_schedule,
    delete_schedule,
    get_schedule,
    list_runs,
    create_run,
    finish_run,
    is_analyzed,
    mark_analyzed,
)

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import threading


PROJECT_ROOT = Path(__file__).parent
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
CREATORS_FILE = PROJECT_ROOT / "creators.json"


def _creator_platform(creator: Any) -> str:
    if isinstance(creator, dict):
        p = str(creator.get("platform") or "douyin").lower().strip()
    else:
        p = str(getattr(creator, "platform", None) or "douyin").lower().strip()
    return p if p in ("douyin", "youtube") else "douyin"


async def fetch_videos_for_creator(creator: Any, max_videos: int) -> List[Dict[str, Any]]:
    """按博主平台拉取最近视频列表。"""
    plat = _creator_platform(creator)
    if isinstance(creator, dict):
        url = str(creator.get("url") or "")
    else:
        url = str(getattr(creator, "url", "") or "")
    if not url:
        return []
    if plat == "youtube":
        return await fetch_youtube_videos(url, max_videos=max_videos)
    return await fetch_creator_videos(url, max_videos=max_videos)


app = FastAPI(
    title="抖音多博主内容分析控制台",
    version="0.1.0",
)

scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
_scheduler_started = False
_run_lock = threading.Lock()

def _read_deepseek_api_key() -> str | None:
    # 兼容 deepseek_analyze.py 的 .env 读取逻辑（不做交互输入）
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    dotenv = PROJECT_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "DEEPSEEK_API_KEY":
                return v.strip().strip('"').strip("'")
    return None


def _strip_md_meta(content: str) -> str:
    lines = (content or "").splitlines()
    start = 0
    if lines and lines[0].lstrip().startswith("<!--"):
        for i in range(1, len(lines)):
            if lines[i].lstrip().startswith("-->"):
                start = i + 1
                break
    while start < len(lines) and not lines[start].strip():
        start += 1
    return "\n".join(lines[start:]).strip()


def _generate_schedule_summary_markdown(processed: list[dict[str, Any]]) -> str | None:
    """
    用 DeepSeek 对本次任务新增/分析的视频做一次总结（Markdown）。
    """
    api_key = _read_deepseek_api_key()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")

    # 读取每条视频的分析报告正文（做长度上限，避免 token 爆炸）
    chunks: list[str] = []
    max_total_chars = 12000
    total = 0
    for item in processed:
        aweme_id = item.get("aweme_id") or ""
        title = item.get("title") or ""
        pattern = str(DOWNLOADS_DIR / f"*{aweme_id}*_deepseek.txt")
        matches = glob.glob(pattern)
        body = ""
        if matches:
            body = _strip_md_meta(Path(matches[0]).read_text(encoding="utf-8"))
        snippet = body[:1200].strip()
        block = f"### {title}\n- aweme_id: {aweme_id}\n- 视频链接: {item.get('video_url')}\n\n{snippet}\n"
        if total + len(block) > max_total_chars:
            break
        chunks.append(block)
        total += len(block)

    context_md = "\n\n".join(chunks) if chunks else ""

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    prompt = f"""你是一位专业的短视频内容分析师/内容运营顾问。下面是某个创作者当天新增分析的视频报告（Markdown片段）。请你输出一份**Markdown**《定时任务总结报告》，要求：

- 只输出 Markdown，不要代码块围栏。
- 使用 `##`/`###` 标题结构化展示，整体左对齐。
- 面向“视频博主”给出：本次更新的核心亮点、可复用的选题方向、可复用的观点/金句、以及下一步内容建议。
- 尽量提炼可直接拿去拍的视频脚本要点（bullet）。

请按以下结构输出：

## 本次更新概览
- 新增分析视频数量：{len(processed)}
- 主题聚类（3-6 条）

## 亮点与可复用观点
- 亮点清单（5-10 条）
- 可复用金句/标题（10 条，短而有冲击力）

## 适合博主的内容建议
### 选题建议（5 条）
### 脚本结构建议（3 条）
### 可直接拍的短视频大纲（3 个）

以下为素材：

{context_md}
"""

    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": "你只输出Markdown，不要输出代码块围栏。"}, {"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1800,
    )
    return (resp.choices[0].message.content or "").strip() or None


def _send_schedule_report_email(
    *,
    to_email: str,
    schedule_name: str,
    schedule_id: int,
    run_id: int,
    processed: list[dict[str, Any]],
    counts: dict[str, Any],
    summary_filename: str | None,
) -> None:
    """
    使用 SMTP 发送任务汇报邮件。需要配置环境变量：
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
    """
    def _get_env_or_dotenv(key: str) -> str:
        v = os.environ.get(key, "").strip()
        if v:
            return v
        dotenv = PROJECT_ROOT / ".env"
        if dotenv.exists():
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, val = line.split("=", 1)
                if k.strip() == key:
                    return val.strip().strip('"').strip("'")
        return ""

    host = _get_env_or_dotenv("SMTP_HOST")
    port = int((_get_env_or_dotenv("SMTP_PORT") or "587").strip() or "587")
    user = _get_env_or_dotenv("SMTP_USER")
    pwd = _get_env_or_dotenv("SMTP_PASS")
    from_email = (_get_env_or_dotenv("SMTP_FROM") or user).strip()
    if not host or not user or not pwd or not from_email:
        raise RuntimeError("SMTP not configured (SMTP_HOST/SMTP_USER/SMTP_PASS/SMTP_FROM)")

    # 用于在邮件中生成可点击链接（本地默认 8000；如部署可在 .env 配 BASE_URL）
    base_url = (_get_env_or_dotenv("BASE_URL") or "http://127.0.0.1:8000").rstrip("/")

    lines = [
        f"定时任务：{schedule_name} (id={schedule_id})",
        f"本次运行：run_id={run_id}",
        "",
        f"候选(candidates)：{counts.get('candidates')}",
        f"计划(planned)：{counts.get('planned')}",
        f"成功(done)：{counts.get('done')}",
        "",
        "本次新增分析视频表格：",
    ]
    # 纯文本：用 TSV 风格表格，便于复制到表格软件
    if processed:
        lines.append("博主\t视频标题\t视频链接\t视频分析结果\t点赞\t评论\t收藏\t分享")
        for item in processed:
            creator = str(item.get("creator_name") or "")
            title = str(item.get("title") or "")
            video_url = str(item.get("video_url") or "")
            aweme_id = str(item.get("aweme_id") or "")
            result_url = f"{base_url}/r/{aweme_id}" if aweme_id else ""
            stats = item.get("stats") or {}
            digg = (stats.get("digg_count") if isinstance(stats, dict) else None) or 0
            comment = (stats.get("comment_count") if isinstance(stats, dict) else None) or 0
            collect = (stats.get("collect_count") if isinstance(stats, dict) else None) or 0
            share = (stats.get("share_count") if isinstance(stats, dict) else None) or 0
            lines.append(f"{creator}\t{title}\t{video_url}\t{result_url}\t{digg}\t{comment}\t{collect}\t{share}")
    else:
        lines.append("（无新增）")

    summary_text: str | None = None
    if summary_filename:
        lines.append("")
        lines.append(f"已生成总结报告：downloads/{summary_filename}")
        try:
            summary_path = DOWNLOADS_DIR / summary_filename
            if summary_path.exists():
                summary_text = summary_path.read_text(encoding="utf-8").strip()
                # 邮件里内嵌一份正文，避免太长（QQ 邮箱也有长度限制）
                max_chars = 8000
                if len(summary_text) > max_chars:
                    summary_text = summary_text[:max_chars] + "\n\n（已截断，完整内容请查看文件）"
                lines.append("")
                lines.append("—— 定时任务总结（DeepSeek）——")
                lines.append(summary_text)
        except Exception as e:
            lines.append("")
            lines.append(f"（读取总结正文失败：{e}）")

    msg = EmailMessage()
    msg["Subject"] = f"[抖音定时任务汇报] {schedule_name} · run {run_id}"
    msg["From"] = from_email
    msg["To"] = to_email
    # 纯文本版本（所有客户端都能看）
    msg.set_content("\n".join(lines))

    # HTML 版本：把 Markdown 总结渲染成富文本（QQ 邮箱可直接展示）
    try:
        import html as _html

        summary_html = ""
        if summary_text:
            try:
                import markdown as _markdown  # type: ignore

                summary_html = _markdown.markdown(
                    summary_text,
                    extensions=["tables", "fenced_code", "sane_lists"],
                    output_format="html5",
                )
            except Exception:
                summary_html = f"<pre style='white-space: pre-wrap; word-break: break-word;'>{_html.escape(summary_text)}</pre>"

        # HTML 表格：博主/标题/链接/分析结果/互动数据
        rows: list[str] = []
        if processed:
            for item in processed:
                creator = _html.escape(str(item.get("creator_name") or ""))
                title = _html.escape(str(item.get("title") or ""))
                vurl = str(item.get("video_url") or "")
                aweme_id = str(item.get("aweme_id") or "")
                stats = item.get("stats") or {}
                if not isinstance(stats, dict):
                    stats = {}
                digg = int(stats.get("digg_count") or 0)
                comment = int(stats.get("comment_count") or 0)
                collect = int(stats.get("collect_count") or 0)
                share = int(stats.get("share_count") or 0)
                video_link = f"<a href='{_html.escape(vurl)}' target='_blank' rel='noreferrer'>打开视频</a>" if vurl else ""
                result_link = (
                    f"<a href='{_html.escape(f'{base_url}/r/{aweme_id}')}' target='_blank' rel='noreferrer'>查看结果</a>"
                    if aweme_id
                    else ""
                )
                rows.append(
                    "<tr>"
                    f"<td style='padding:8px 10px; border:1px solid #e5e7eb; vertical-align:top; white-space:nowrap;'>{creator}</td>"
                    f"<td style='padding:8px 10px; border:1px solid #e5e7eb; vertical-align:top;'>{title}</td>"
                    f"<td style='padding:8px 10px; border:1px solid #e5e7eb; vertical-align:top; white-space:nowrap;'>{video_link}</td>"
                    f"<td style='padding:8px 10px; border:1px solid #e5e7eb; vertical-align:top; white-space:nowrap;'>{result_link}</td>"
                    f"<td style='padding:8px 10px; border:1px solid #e5e7eb; vertical-align:top; text-align:right; white-space:nowrap;'>{digg}</td>"
                    f"<td style='padding:8px 10px; border:1px solid #e5e7eb; vertical-align:top; text-align:right; white-space:nowrap;'>{comment}</td>"
                    f"<td style='padding:8px 10px; border:1px solid #e5e7eb; vertical-align:top; text-align:right; white-space:nowrap;'>{collect}</td>"
                    f"<td style='padding:8px 10px; border:1px solid #e5e7eb; vertical-align:top; text-align:right; white-space:nowrap;'>{share}</td>"
                    "</tr>"
                )

        table_html = ""
        attachment_added = False
        max_inline_rows = 35
        if rows and len(rows) <= max_inline_rows:
            table_html = (
                "<div style='margin-top:8px; overflow:auto;'>"
                "<table style='border-collapse:collapse; width:100%; font-size:13px;'>"
                "<thead><tr>"
                "<th style='text-align:left; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>博主</th>"
                "<th style='text-align:left; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>视频标题</th>"
                "<th style='text-align:left; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>视频链接</th>"
                "<th style='text-align:left; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>视频分析结果</th>"
                "<th style='text-align:right; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>点赞</th>"
                "<th style='text-align:right; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>评论</th>"
                "<th style='text-align:right; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>收藏</th>"
                "<th style='text-align:right; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>分享</th>"
                "</tr></thead>"
                "<tbody>"
                + "".join(rows)
                + "</tbody></table></div>"
            )
        elif rows:
            # 行数太多：生成 CSV 附件，邮件内只放前几行提示
            import csv, io

            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["博主", "视频标题", "视频链接", "视频分析结果", "点赞", "评论", "收藏", "分享"])
            for item in processed:
                creator = str(item.get("creator_name") or "")
                title = str(item.get("title") or "")
                vurl = str(item.get("video_url") or "")
                aweme_id = str(item.get("aweme_id") or "")
                result_url = f"{base_url}/r/{aweme_id}" if aweme_id else ""
                stats = item.get("stats") or {}
                if not isinstance(stats, dict):
                    stats = {}
                digg = int(stats.get("digg_count") or 0)
                comment = int(stats.get("comment_count") or 0)
                collect = int(stats.get("collect_count") or 0)
                share = int(stats.get("share_count") or 0)
                w.writerow([creator, title, vurl, result_url, digg, comment, collect, share])
            csv_text = buf.getvalue()
            msg.add_attachment(
                csv_text.encode("utf-8-sig"),
                maintype="text",
                subtype="csv",
                filename=f"schedule_{schedule_id}_run_{run_id}_videos.csv",
            )
            attachment_added = True
            preview_rows = rows[:8]
            table_html = (
                "<div style='margin-top:8px; font-size:13px; color:#6b7280;'>"
                "本次视频较多，已生成 CSV 附件（邮件内仅预览前几条）。"
                "</div>"
                "<div style='margin-top:8px; overflow:auto;'>"
                "<table style='border-collapse:collapse; width:100%; font-size:13px;'>"
                "<thead><tr>"
                "<th style='text-align:left; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>博主</th>"
                "<th style='text-align:left; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>视频标题</th>"
                "<th style='text-align:left; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>视频链接</th>"
                "<th style='text-align:left; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>视频分析结果</th>"
                "<th style='text-align:right; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>点赞</th>"
                "<th style='text-align:right; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>评论</th>"
                "<th style='text-align:right; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>收藏</th>"
                "<th style='text-align:right; padding:8px 10px; border:1px solid #e5e7eb; background:#f9fafb;'>分享</th>"
                "</tr></thead><tbody>"
                + "".join(preview_rows)
                + "</tbody></table></div>"
            )

        html_body = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
</head>
<body style="margin:0; padding:0; background:#f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;">
  <div style="max-width: 920px; margin: 0 auto; padding: 18px;">
    <div style="background:#ffffff; border:1px solid #e5e7eb; border-radius: 12px; padding: 16px;">
      <div style="font-size: 18px; font-weight: 800; color:#111827;">抖音定时任务汇报</div>
      <div style="margin-top: 6px; font-size: 13px; color:#374151;">
        <div>定时任务：{_html.escape(schedule_name)}（id={schedule_id}）</div>
        <div>本次运行：run_id={run_id}</div>
      </div>

      <div style="margin-top: 12px; display:flex; gap:10px; flex-wrap:wrap;">
        <div style="background:#f3f4f6; border-radius: 999px; padding: 6px 10px; font-size: 12px; color:#111827;">候选 {counts.get("candidates")}</div>
        <div style="background:#f3f4f6; border-radius: 999px; padding: 6px 10px; font-size: 12px; color:#111827;">计划 {counts.get("planned")}</div>
        <div style="background:#dcfce7; border-radius: 999px; padding: 6px 10px; font-size: 12px; color:#14532d;">成功 {counts.get("done")}</div>
      </div>

      <div style="margin-top: 14px;">
        <div style="font-size: 14px; font-weight: 800; color:#111827;">本次新增分析视频（表格）</div>
        {table_html if table_html else "<div style='margin-top:6px; font-size: 13px; color:#6b7280;'>（无新增）</div>"}
      </div>

      {f"""
      <div style="margin-top: 16px; border-top:1px solid #e5e7eb; padding-top: 14px;">
        <div style="font-size: 14px; font-weight: 800; color:#111827;">定时任务总结（DeepSeek）</div>
        <div style="margin-top: 8px; font-size: 14px; line-height: 1.7; color:#111827;">
          {summary_html}
        </div>
        <div style="margin-top: 10px; font-size: 12px; color:#6b7280;">文件：downloads/{_html.escape(summary_filename or "")}</div>
      </div>
      """ if summary_filename and summary_text else ""}
    </div>
    <div style="margin-top: 10px; font-size: 12px; color:#94a3b8; text-align:center;">由抖音内容分析工作台自动发送</div>
  </div>
</body>
</html>
""".strip()

        msg.add_alternative(html_body, subtype="html")
    except Exception:
        # HTML 渲染失败则只发纯文本
        pass

    # QQ SMTP：587 用 STARTTLS；465 用 SSL
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20) as s:
            s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, pwd)
            s.send_message(msg)


def _find_deepseek_filename_for_aweme(aweme_id: str) -> str | None:
    pattern = str(DOWNLOADS_DIR / f"*{aweme_id}*_deepseek.txt")
    matches = glob.glob(pattern)
    if not matches:
        return None
    return Path(matches[0]).name


def _find_raw_transcript_for_aweme(aweme_id: str) -> Path | None:
    """downloads 下包含 aweme_id 的原始转录 .txt（排除分析报告与全文阅读稿）。"""
    pattern = str(DOWNLOADS_DIR / f"*{aweme_id}*.txt")
    matches = glob.glob(pattern)
    candidates: list[Path] = []
    for m in matches:
        p = Path(m)
        s = p.stem
        if "_deepseek" in s or s.endswith("_deepseek"):
            continue
        if "_readable" in s or s.endswith("_readable"):
            continue
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.stat().st_mtime)


def _find_readable_transcript_for_aweme(aweme_id: str) -> Path | None:
    pattern = str(DOWNLOADS_DIR / f"*{aweme_id}*_readable.txt")
    matches = glob.glob(pattern)
    if not matches:
        return None
    return Path(max(matches, key=lambda p: Path(p).stat().st_mtime))


def _ensure_readable_transcript_sync(aweme_id: str, force: bool) -> Dict[str, Any]:
    from deepseek_analyze import (
        polish_transcript_for_reading,
        read_transcript,
        save_readable_transcript,
    )

    if not force:
        rp = _find_readable_transcript_for_aweme(aweme_id)
        if rp and rp.exists():
            body = _strip_md_meta(rp.read_text(encoding="utf-8"))
            return {
                "aweme_id": aweme_id,
                "filename": rp.name,
                "text": body,
                "cached": True,
            }

    key = _read_deepseek_api_key()
    if not key:
        raise HTTPException(status_code=503, detail="未配置 DEEPSEEK_API_KEY")

    raw = _find_raw_transcript_for_aweme(aweme_id)
    if not raw or not raw.exists():
        raise HTTPException(
            status_code=400,
            detail="未找到逐字稿。请先点击「分析这条视频」完成下载与转录。",
        )

    text = read_transcript(raw)
    if not (text or "").strip():
        raise HTTPException(status_code=400, detail="转录文件为空")

    try:
        polished = polish_transcript_for_reading(key, text)
    except AuthenticationError:
        raise HTTPException(status_code=503, detail="DeepSeek API Key 无效")
    except RateLimitError:
        raise HTTPException(status_code=503, detail="DeepSeek 余额不足或触发限速")
    except APIError as e:
        raise HTTPException(status_code=502, detail=f"DeepSeek API 错误：{e}")
    except Exception as e:
        print("❌ 全文阅读 DeepSeek 异常：", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=502, detail=f"整理失败：{e}")

    if not (polished or "").strip():
        raise HTTPException(status_code=502, detail="整理结果为空")

    save_readable_transcript(raw, polished)
    rp = _find_readable_transcript_for_aweme(aweme_id)
    return {
        "aweme_id": aweme_id,
        "filename": rp.name if rp else "",
        "text": polished.strip(),
        "cached": False,
    }


async def _execute_schedule(schedule_id: int) -> None:
    sch = get_schedule(schedule_id)
    if not sch or not sch.enabled:
        return
    # 防止同一时刻重复执行（开发 reload / 多触发）
    if not _run_lock.acquire(blocking=False):
        return

    run_id = create_run(schedule_id)
    try:
        creators = load_creators()
        indices = sch.creator_indices or list(range(len(creators)))
        window_size = 20
        total_candidates = 0
        total_planned = 0
        total_done = 0
        processed: list[dict[str, Any]] = []

        for idx in indices:
            if idx < 0 or idx >= len(creators):
                continue
            creator = creators[idx]
            # 兼容 creators.json 的 dict 结构 / 以及 load_creators() 返回的对象结构
            if isinstance(creator, dict):
                creator_name = str(creator.get("name") or "")
                creator_url = str(creator.get("url") or "")
            else:
                creator_name = str(getattr(creator, "name", "") or "")
                creator_url = str(getattr(creator, "url", "") or "")

            creator_obj = creators[idx]
            try:
                videos = await fetch_videos_for_creator(creator_obj, max_videos=window_size)
            except Exception as e:
                print("⚠️ schedule fetch videos failed:", idx, repr(e))
                continue
            if not videos:
                continue

            # 最新优先
            videos_sorted = sorted(videos, key=lambda v: v.get("create_time") or 0, reverse=True)
            total_candidates += len(videos_sorted)

            picked = []
            for v in videos_sorted:
                aweme_id = str(v.get("aweme_id") or "")
                if not aweme_id:
                    continue
                # 全局去重：任何人处理过就跳过
                if is_analyzed(aweme_id) or _is_video_analyzed(aweme_id):
                    continue
                picked.append(v)
                if len(picked) >= sch.max_new_per_creator:
                    break

            total_planned += len(picked)
            for v in picked:
                aweme_id = str(v.get("aweme_id") or "")
                title = v.get("title") or ""
                video_url = v.get("video_url") or ""
                try:
                    await run_pipeline(video_url)
                    total_done += 1
                    deepseek_fn = _find_deepseek_filename_for_aweme(aweme_id)
                    mark_analyzed(
                        aweme_id=aweme_id,
                        title=title,
                        video_url=video_url,
                        creator_url=creator_url,
                        deepseek_filename=deepseek_fn,
                    )
                    processed.append(
                        {
                            "aweme_id": aweme_id,
                            "title": title,
                            "video_url": video_url,
                            "creator_url": creator_url,
                            "creator_name": creator_name,
                            "deepseek_filename": deepseek_fn,
                            "stats": v.get("stats") or {},
                        }
                    )
                except Exception as e:
                    # 单条失败不影响全局，记录在 run detail
                    print("⚠️ schedule video failed:", aweme_id, repr(e))

        summary_filename = None
        if getattr(sch, "generate_summary", False) and processed:
            try:
                summary_md = _generate_schedule_summary_markdown(processed)
                if summary_md:
                    summary_filename = f"schedule_{schedule_id}_run_{run_id}_summary.md"
                    (DOWNLOADS_DIR / summary_filename).write_text(summary_md, encoding="utf-8")
            except Exception as e:
                print("⚠️ schedule summary failed:", repr(e))

        email_sent = False
        email_error = None
        if getattr(sch, "report_email", None):
            try:
                _send_schedule_report_email(
                    to_email=sch.report_email,
                    schedule_name=sch.name,
                    schedule_id=schedule_id,
                    run_id=run_id,
                    processed=processed,
                    counts={
                        "candidates": total_candidates,
                        "planned": total_planned,
                        "done": total_done,
                    },
                    summary_filename=summary_filename,
                )
                email_sent = True
            except Exception as e:
                email_error = repr(e)
                print("⚠️ schedule email failed:", email_error)

        finish_run(
            run_id,
            status="success",
            detail=json.dumps(
                {
                    "candidates": total_candidates,
                    "planned": total_planned,
                    "done": total_done,
                    "summary_filename": summary_filename,
                    "email": {
                        "to": getattr(sch, "report_email", None),
                        "sent": email_sent,
                        "error": email_error,
                    },
                },
                ensure_ascii=False,
            ),
        )
    except Exception as e:
        finish_run(run_id, status="failed", detail=repr(e))
        raise
    finally:
        _run_lock.release()


def _execute_schedule_sync(schedule_id: int) -> None:
    asyncio.run(_execute_schedule(schedule_id))


def _refresh_scheduler_jobs() -> None:
    # 清理旧 job
    for job in scheduler.get_jobs():
        if job.id.startswith("schedule:"):
            scheduler.remove_job(job.id)

    for sch in list_schedules():
        if not sch.enabled:
            continue
        job_id = f"schedule:{sch.id}"
        trigger = CronTrigger(hour=sch.hour, minute=sch.minute)
        scheduler.add_job(
            lambda sid=sch.id: _execute_schedule_sync(sid),
            trigger=trigger,
            id=job_id,
            replace_existing=True,
        )


@app.on_event("startup")
async def _on_startup() -> None:
    global _scheduler_started
    init_db()
    if not _scheduler_started:
        scheduler.start()
        _scheduler_started = True
        _refresh_scheduler_jobs()


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)

# 允许本机浏览器访问（如需部署可再收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# 首页：返回一个响应式 HTML（Tailwind CDN）
# ──────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """简单的单页应用，使用原生 JS + Tailwind，兼顾电脑和手机。"""
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>抖音多博主内容分析控制台</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 text-slate-900">
  <div class="min-h-screen flex flex-col">
    <!-- 顶部导航 -->
    <header class="bg-white border-b border-slate-200">
      <div class="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
        <div class="flex items-center space-x-2">
          <span class="text-xl font-semibold">抖音内容分析工作台</span>
          <span class="text-xs text-slate-500 hidden sm:inline">多博主 · 多视频 · 一键分析</span>
        </div>
        <div class="text-xs text-slate-400">
          后端：FastAPI · 前端：原生 JS + Tailwind
        </div>
      </div>
    </header>

    <!-- 主体 -->
    <main class="flex-1 max-w-6xl mx-auto w-full px-4 py-4 space-y-4">
      <!-- 博主列表 -->
      <section class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3">
          <h2 class="text-lg font-semibold">关注的博主</h2>
          <button id="btn-refresh-creators"
                  class="inline-flex items-center justify-center rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800">
            刷新列表
          </button>
        </div>
        <div id="creators-container" class="space-y-2 text-sm">
          <p class="text-slate-500">正在加载 creators.json 中的博主配置...</p>
        </div>
      </section>

      <!-- 视频列表 -->
      <section class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3">
          <h2 class="text-lg font-semibold">博主最近视频</h2>
          <p id="videos-meta" class="text-xs text-slate-500"></p>
        </div>
        <div id="videos-container" class="space-y-3 text-sm">
          <p class="text-slate-500">请选择上方的某个博主查看最近视频。</p>
        </div>
      </section>

      <!-- 日志区域 -->
      <section class="bg-slate-900 rounded-xl shadow-sm border border-slate-800 p-3">
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-sm font-semibold text-slate-100">任务日志</h2>
          <button id="btn-clear-log"
                  class="text-xs text-slate-400 hover:text-slate-200">
            清空
          </button>
        </div>
        <pre id="log"
             class="text-xs text-slate-100 bg-slate-900 rounded-lg max-h-64 overflow-auto whitespace-pre-wrap"></pre>
      </section>
    </main>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    const logEl = $("log");

    function log(msg) {
      const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      logEl.textContent += `[${ts}] ${msg}\\n`;
      logEl.scrollTop = logEl.scrollHeight;
    }

    $("btn-clear-log").addEventListener("click", () => {
      logEl.textContent = "";
    });

    async function fetchJSON(url, options) {
      const res = await fetch(url, options);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text}`);
      }
      return await res.json();
    }

    async function loadCreators() {
      try {
        const data = await fetchJSON("/api/creators");
        const container = $("creators-container");
        if (!data.length) {
          container.innerHTML = '<p class="text-slate-500">creators.json 中还没有配置博主。</p>';
          return;
        }
        container.innerHTML = "";
        data.forEach((c, idx) => {
          const btn = document.createElement("button");
          btn.className = "w-full flex flex-col sm:flex-row sm:items-center sm:justify-between px-3 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 text-left";
          btn.innerHTML = `
            <div>
              <div class="font-medium">${c.name}</div>
              <div class="text-xs text-slate-500 truncate max-w-full sm:max-w-md">${c.url}</div>
            </div>
            <div class="mt-1 sm:mt-0 text-xs text-slate-500">
              每次最多新视频：<span class="font-semibold">${c.max_new_videos}</span>
            </div>
          `;
          btn.addEventListener("click", () => {
            loadVideosForCreator(idx, c);
          });
          container.appendChild(btn);
        });
      } catch (err) {
        $("creators-container").innerHTML = '<p class="text-red-600 text-sm">加载博主配置失败：' + err.message + '</p>';
      }
    }

    async function loadVideosForCreator(index, creator) {
      $("videos-container").innerHTML = '<p class="text-slate-500">正在加载该博主的最近视频...</p>';
      $("videos-meta").textContent = "";
      try {
        const videos = await fetchJSON(`/api/creators/${index}/videos`);
        if (!videos.length) {
          $("videos-container").innerHTML = '<p class="text-slate-500">未能获取到视频列表。</p>';
          return;
        }
        $("videos-meta").textContent = `共 ${videos.length} 条视频（按时间倒序）`;

        const container = $("videos-container");
        container.innerHTML = "";
        videos.forEach((v, i) => {
          const stats = v.stats || {};
          const card = document.createElement("div");
          card.className = "border border-slate-200 rounded-lg p-3 flex flex-col gap-2";
          card.innerHTML = `
            <div class="flex justify-between gap-2">
              <div class="text-sm font-medium line-clamp-2">${v.title || "（无标题）"}</div>
              <div class="text-xs text-slate-500 shrink-0">#${i + 1}</div>
            </div>
            <a href="${v.video_url}" target="_blank" class="text-xs text-blue-600 hover:underline break-all">${v.video_url}</a>
            <div class="flex flex-wrap items-center gap-3 text-xs text-slate-600">
              <span>👍 ${stats.digg_count ?? 0}</span>
              <span>💬 ${stats.comment_count ?? 0}</span>
              <span>🔁 ${stats.share_count ?? 0}</span>
              <span>⭐ ${stats.collect_count ?? 0}</span>
            </div>
            <div>
              <button class="btn-analyze inline-flex items-center justify-center rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500">
                分析这条视频
              </button>
              <span class="ml-2 text-xs text-slate-500 status-text"></span>
            </div>
          `;
          const btn = card.querySelector(".btn-analyze");
          const status = card.querySelector(".status-text");
          btn.addEventListener("click", async () => {
            btn.disabled = true;
            btn.classList.add("opacity-60");
            status.textContent = "排队分析中...";
            log(`开始分析：${v.title || v.video_url}`);
            try {
              const res = await fetchJSON("/api/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ video_url: v.video_url }),
              });
              status.textContent = "分析完成 ✅";
              log(`完成分析：${v.title || v.video_url}`);
            } catch (err) {
              status.textContent = "分析失败 ❌";
              log(`分析失败：${v.video_url} -> ${err.message}`);
              btn.disabled = false;
              btn.classList.remove("opacity-60");
            }
          });
          container.appendChild(card);
        });
      } catch (err) {
        $("videos-container").innerHTML = '<p class="text-red-600 text-sm">加载视频列表失败：' + err.message + '</p>';
      }
    }

    $("btn-refresh-creators").addEventListener("click", loadCreators);

    // 页面加载完成后自动拉取一次
    loadCreators();
  </script>
</body>
</html>
    """


# ──────────────────────────────────────────────
# API：博主列表与视频列表
# ──────────────────────────────────────────────


@app.get("/api/creators")
async def api_creators() -> List[Dict[str, Any]]:
    """返回 creators.json 中的博主列表。"""
    creators = load_creators()
    return [
        {
            "name": c.name,
            "url": c.url,
            "max_new_videos": c.max_new_videos,
            "platform": getattr(c, "platform", "douyin"),
        }
        for c in creators
    ]


class CreatorIn(BaseModel):
    name: str
    url: str
    max_new_videos: int = 5
    platform: str = "douyin"

    @field_validator("platform")
    @classmethod
    def _validate_platform(cls, v: str) -> str:
        x = (v or "douyin").lower().strip()
        if x not in ("douyin", "youtube"):
            raise ValueError("platform must be douyin or youtube")
        return x


@app.post("/api/creators")
async def api_create_creator(creator: CreatorIn) -> List[Dict[str, Any]]:
    """新增博主配置并写回 creators.json。"""
    data: List[Dict[str, Any]] = (
        json.loads(CREATORS_FILE.read_text(encoding="utf-8"))
        if CREATORS_FILE.exists()
        else []
    )
    data.append(creator.model_dump())
    CREATORS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data


@app.put("/api/creators/{index}")
async def api_update_creator(index: int, creator: CreatorIn) -> List[Dict[str, Any]]:
    """按索引更新博主配置。"""
    data: List[Dict[str, Any]] = (
        json.loads(CREATORS_FILE.read_text(encoding="utf-8"))
        if CREATORS_FILE.exists()
        else []
    )
    if index < 0 or index >= len(data):
        raise HTTPException(status_code=404, detail="creator index out of range")
    data[index] = creator.model_dump()
    CREATORS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data


@app.delete("/api/creators/{index}")
async def api_delete_creator(index: int) -> Dict[str, Any]:
    """按索引删除博主配置。"""
    data: List[Dict[str, Any]] = (
        json.loads(CREATORS_FILE.read_text(encoding="utf-8"))
        if CREATORS_FILE.exists()
        else []
    )
    if index < 0 or index >= len(data):
        raise HTTPException(status_code=404, detail="creator index out of range")
    deleted = data.pop(index)
    CREATORS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"deleted": deleted, "creators": data}


def _is_video_analyzed(aweme_id: str) -> bool:
    """判断某个 aweme 是否已有 DeepSeek 分析结果。"""
    pattern = str(DOWNLOADS_DIR / f"*{aweme_id}*_deepseek.txt")
    return bool(glob.glob(pattern))


@app.get("/api/creators/{index}/videos")
async def api_creator_videos(
    index: int,
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=50),
) -> Any:
    """获取指定索引的博主最近视频列表，并标记是否已分析。支持分页。"""
    creators = load_creators()
    if index < 0 or index >= len(creators):
        raise HTTPException(status_code=404, detail="creator index out of range")
    creator = creators[index]
    use_paging = page is not None or page_size is not None
    p = int(page or 1)
    ps = int(page_size or 10)
    if not use_paging:
        p = 1
        ps = 10
    start = (p - 1) * ps
    end = start + ps
    try:
        # 取 end+1 用于判断是否还有下一页（has_more）
        videos = await fetch_videos_for_creator(creator, max_videos=end + 1)
    except Exception as e:
        print("❌ /api/creators/*/videos 异常：", repr(e))
        print(traceback.format_exc())
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
            raise HTTPException(
                status_code=503,
                detail="Playwright 浏览器未安装。请在项目目录执行：.venv/bin/playwright install chromium",
            ) from e
        raise HTTPException(status_code=503, detail=f"抓取视频列表失败：{msg}") from e

    plat = _creator_platform(creator)
    # has_more 判断：拿了 end+1 条，若超过 end 则说明还有下一页
    has_more = bool(use_paging and len(videos) > end)
    window = videos[start:end] if use_paging else videos[:10]

    enriched: List[Dict[str, Any]] = []
    for v in window:
        aweme_id = str(v.get("aweme_id", ""))
        v["analyzed"] = bool(aweme_id and _is_video_analyzed(aweme_id))
        if "platform" not in v:
            v["platform"] = plat
        enriched.append(v)

    if use_paging:
        return {
            "items": enriched,
            "page": p,
            "page_size": ps,
            "has_more": has_more,
        }
    return enriched


# ──────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    video_url: AnyHttpUrl
    aweme_id: str | None = None
    # douyin | youtube；未传时由 run_pipeline 根据链接推断（YouTube 链接固定用 YouTube 模板）
    analysis_mode: str | None = None

    @field_validator("analysis_mode")
    @classmethod
    def _validate_analysis_mode(cls, v: str | None) -> str | None:
        if v is None or str(v).strip() == "":
            return None
        x = str(v).lower().strip()
        if x not in ("douyin", "youtube"):
            raise ValueError("analysis_mode must be douyin or youtube")
        return x


class ReadableTranscriptRequest(BaseModel):
    aweme_id: str
    force: bool = False


class ScheduleIn(BaseModel):
    name: str
    enabled: bool = True
    hour: int
    minute: int
    max_new_per_creator: int = 5
    creator_indices: List[int] = []
    report_email: str | None = None
    generate_summary: bool = False


def _validate_schedule_in(s: ScheduleIn) -> None:
    if not s.name.strip():
        raise HTTPException(status_code=400, detail="name required")
    if s.hour < 0 or s.hour > 23:
        raise HTTPException(status_code=400, detail="hour out of range")
    if s.minute < 0 or s.minute > 59:
        raise HTTPException(status_code=400, detail="minute out of range")
    if s.max_new_per_creator < 1 or s.max_new_per_creator > 50:
        raise HTTPException(status_code=400, detail="max_new_per_creator out of range")
    if any((not isinstance(i, int)) for i in s.creator_indices):
        raise HTTPException(status_code=400, detail="creator_indices must be ints")
    if s.report_email:
        email = s.report_email.strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise HTTPException(status_code=400, detail="report_email invalid")


@app.post("/api/analyze")
async def api_analyze(req: AnalyzeRequest) -> Dict[str, Any]:
    """
    对单条视频运行完整流水线：
      下载音频 → AssemblyAI 转录 → DeepSeek 分析。

    注意：这是一个耗时操作（几十秒到数分钟），前端已做简单的 loading 提示。
    如果已经存在对应 aweme_id 的 DeepSeek 报告，则直接复用，不再重复下载与分析。
    """
    # 如果前端提供了 aweme_id，且已存在分析结果，则直接返回
    if req.aweme_id:
        if _is_video_analyzed(req.aweme_id):
            return {"ok": True, "cached": True}

    try:
        await run_pipeline(str(req.video_url), analysis_mode=req.analysis_mode)
    except SystemExit as e:
        # main.py 中在错误时可能会 sys.exit
        print("❌ pipeline SystemExit:", e)
        raise HTTPException(status_code=500, detail=f"pipeline exited with code {e.code}")
    except Exception as e:
        print("❌ /api/analyze 异常：", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True}


@app.get("/api/schedules")
async def api_list_schedules() -> List[Dict[str, Any]]:
    return [
        {
            "id": s.id,
            "name": s.name,
            "enabled": s.enabled,
            "hour": s.hour,
            "minute": s.minute,
            "max_new_per_creator": s.max_new_per_creator,
            "creator_indices": s.creator_indices,
            "report_email": getattr(s, "report_email", None),
            "generate_summary": bool(getattr(s, "generate_summary", False)),
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in list_schedules()
    ]


@app.post("/api/schedules")
async def api_create_schedule(s: ScheduleIn) -> Dict[str, Any]:
    _validate_schedule_in(s)
    sch = create_schedule(
        name=s.name.strip(),
        enabled=bool(s.enabled),
        hour=s.hour,
        minute=s.minute,
        max_new_per_creator=s.max_new_per_creator,
        creator_indices=s.creator_indices or [],
        report_email=s.report_email.strip() if s.report_email else None,
        generate_summary=bool(s.generate_summary),
    )
    _refresh_scheduler_jobs()
    return {"ok": True, "schedule": {"id": sch.id}}


@app.put("/api/schedules/{schedule_id}")
async def api_update_schedule(schedule_id: int, s: ScheduleIn) -> Dict[str, Any]:
    _validate_schedule_in(s)
    sch = update_schedule(
        schedule_id,
        name=s.name.strip(),
        enabled=bool(s.enabled),
        hour=s.hour,
        minute=s.minute,
        max_new_per_creator=s.max_new_per_creator,
        creator_indices=s.creator_indices or [],
        report_email=s.report_email.strip() if s.report_email else None,
        generate_summary=bool(s.generate_summary),
    )
    if not sch:
        raise HTTPException(status_code=404, detail="schedule not found")
    _refresh_scheduler_jobs()
    return {"ok": True}


@app.delete("/api/schedules/{schedule_id}")
async def api_delete_schedule(schedule_id: int) -> Dict[str, Any]:
    delete_schedule(schedule_id)
    _refresh_scheduler_jobs()
    return {"ok": True}


@app.get("/api/schedules/{schedule_id}/runs")
async def api_list_runs(schedule_id: int) -> Dict[str, Any]:
    if not get_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found")
    return {"ok": True, "runs": list_runs(schedule_id, limit=20)}


@app.post("/api/schedules/{schedule_id}/run")
async def api_run_schedule_now(schedule_id: int) -> Dict[str, Any]:
    if not get_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found")
    # 在独立线程执行，避免阻塞 API 事件循环
    t = threading.Thread(target=_execute_schedule_sync, args=(schedule_id,), daemon=True)
    t.start()
    return {"ok": True}

@app.get("/api/video/result")
async def api_video_result(aweme_id: str = Query(...)) -> Dict[str, Any]:
    """
    根据 aweme_id 返回 DeepSeek 分析结果全文。
    在 downloads/ 目录中查找包含 aweme_id 且以 _deepseek.txt 结尾的文件。
    """
    pattern = str(DOWNLOADS_DIR / f"*{aweme_id}*_deepseek.txt")
    matches = glob.glob(pattern)
    if not matches:
        raise HTTPException(status_code=404, detail="result not found")
    path = Path(matches[0])
    content = path.read_text(encoding="utf-8")
    # 去掉 deepseek_analyze.py 写入的文件头元信息，只保留正文（Markdown）
    content_lines = content.splitlines()
    start_idx = 0
    if content_lines and content_lines[0].lstrip().startswith("<!--"):
        for i in range(1, len(content_lines)):
            if content_lines[i].lstrip().startswith("-->"):
                start_idx = i + 1
                break
    # 跳过开头空行
    while start_idx < len(content_lines) and not content_lines[start_idx].strip():
        start_idx += 1
    body = "\n".join(content_lines[start_idx:]).strip()
    return {
        "aweme_id": aweme_id,
        "filename": path.name,
        "content": content,
        "content_body": body,
    }


@app.get("/api/video/readable-transcript")
async def api_readable_transcript_get(aweme_id: str = Query(...)) -> Dict[str, Any]:
    """仅返回已缓存的全文阅读稿（不调用 DeepSeek）。"""
    rp = _find_readable_transcript_for_aweme(aweme_id.strip())
    if not rp or not rp.exists():
        raise HTTPException(status_code=404, detail="全文阅读稿尚未生成")
    body = _strip_md_meta(rp.read_text(encoding="utf-8"))
    return {
        "aweme_id": aweme_id.strip(),
        "filename": rp.name,
        "text": body,
        "cached": True,
    }


@app.post("/api/video/readable-transcript")
async def api_readable_transcript_post(req: ReadableTranscriptRequest) -> Dict[str, Any]:
    """
    生成或返回全文阅读稿：基于 ASR 转录调用 DeepSeek 做翻译、标点与上下文纠偏。
    已有缓存且 force=false 时直接返回；force=true 时重新生成并覆盖对应 *_readable.txt。
    """
    aid = (req.aweme_id or "").strip()
    if not aid:
        raise HTTPException(status_code=400, detail="aweme_id 必填")
    return await asyncio.to_thread(_ensure_readable_transcript_sync, aid, bool(req.force))


@app.get("/r/{aweme_id}", response_class=HTMLResponse)
async def page_result(aweme_id: str) -> str:
    """
    轻量结果页：便于在邮件中放“查看结果”链接。
    """
    pattern = str(DOWNLOADS_DIR / f"*{aweme_id}*_deepseek.txt")
    matches = glob.glob(pattern)
    if not matches:
        raise HTTPException(status_code=404, detail="result not found")
    path = Path(matches[0])
    md_body = _strip_md_meta(path.read_text(encoding="utf-8"))
    try:
        import markdown as _markdown  # type: ignore

        html_body = _markdown.markdown(md_body, extensions=["tables", "sane_lists"], output_format="html5")
    except Exception:
        import html as _html

        html_body = f"<pre style='white-space: pre-wrap; word-break: break-word;'>{_html.escape(md_body)}</pre>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>视频分析结果 {aweme_id}</title>
</head>
<body style="margin:0; padding:0; background:#f8fafc; font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;">
  <div style="max-width: 980px; margin: 0 auto; padding: 18px;">
    <div style="background:#ffffff; border:1px solid #e5e7eb; border-radius: 12px; padding: 16px;">
      <div style="font-size: 18px; font-weight: 800; color:#111827;">视频分析结果</div>
      <div style="margin-top: 6px; font-size: 12px; color:#6b7280;">aweme_id: {aweme_id} · 文件：{path.name}</div>
      <div style="margin-top: 14px; font-size: 14px; line-height: 1.75; color:#111827;">
        {html_body}
      </div>
    </div>
  </div>
</body>
</html>"""
