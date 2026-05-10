#!/usr/bin/env python3
"""
抖音登录 & Cookie 保存工具
============================
用法（在远程机器上直接运行）：
    cd ~/douyin-project
    .venv/bin/python scripts/douyin_login.py

会打开一个可见的 Chromium 浏览器窗口，手动扫码/登录后
按回车，Cookie 自动保存到 www.douyin.com_cookies.json。
"""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

COOKIE_FILE = Path(__file__).parent.parent / "www.douyin.com_cookies.json"


async def main():
    print("=" * 50)
    print("  抖音登录工具")
    print("=" * 50)
    print("▶ 正在启动浏览器，请稍候...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto("https://www.douyin.com", wait_until="domcontentloaded")

        print("\n✅ 浏览器已打开，请在浏览器中完成抖音登录（扫码或账号密码）")
        print("   登录成功后，回到此终端按回车保存 Cookie\n")
        input("   >>> 登录完成后按回车 <<<\n")

        cookies = await context.cookies()
        douyin_cookies = [c for c in cookies if "douyin.com" in c.get("domain", "")]

        if not douyin_cookies:
            print("⚠️  未检测到抖音 Cookie，请确认已登录成功")
            await browser.close()
            return

        COOKIE_FILE.write_text(json.dumps(douyin_cookies, ensure_ascii=False, indent=2))
        print(f"✅ 已保存 {len(douyin_cookies)} 个 Cookie → {COOKIE_FILE}")
        await browser.close()
        print("🎉 完成，后端下次请求时将自动使用新 Cookie")


if __name__ == "__main__":
    asyncio.run(main())
