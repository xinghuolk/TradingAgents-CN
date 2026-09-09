"""Research final-wave browser matrix using the real API and in-memory storage."""

import argparse
import asyncio
import json
import re
import sys
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.routers import stock_research
from app.routers.auth_db import get_current_user
from app.services.stock_research.models import NewEntry, Reference, ThesisPatch
from tests.unit.stock_research.test_generation import setup_generation
from tests.unit.stock_research.test_references import FakeRealPortfolioService
from tests.unit.test_stock_research_router import create_test_app, create_test_client


async def fixture():
    generation, _, repo, service, review = await setup_generation()
    repo.db["stock_research_workspaces"].documents.clear()
    repo.db["stock_research_entries"].documents.clear()
    generation.references.real_portfolio = FakeRealPortfolioService()
    await repo.ensure_indexes()
    await repo.db["paper_trades"].insert_one({"_id": "paper-one", "user_id": "u1", "market": "CN", "code": "600519", "timestamp": "2026-09-08", "side": "buy", "quantity": 10, "price": 1500})
    application = create_test_app(service)
    application.dependency_overrides[get_current_user] = lambda: {"id": "u1"}
    application.dependency_overrides[stock_research.get_reference_service] = lambda: generation.references
    application.dependency_overrides[stock_research.get_generation_service] = lambda: generation
    return create_test_client(application), service, repo


def run(args, viewport):
    suffix = "desktop" if viewport["width"] == 1440 else "mobile"
    with asyncio.Runner() as runner, ThreadPoolExecutor(max_workers=1) as backend:
        def call(coroutine):
            return backend.submit(runner.run, coroutine).result()
        client, service, repo = call(fixture())
        errors, console_errors, requests = [], [], []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=args.chromium, args=["--no-sandbox"])
            page = browser.new_page(viewport=viewport)
            page.set_default_timeout(15000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.add_init_script("""
                localStorage.setItem('auth-token', 'eyJhbGciOiJub25lIn0.eyJleHAiOjQxMDI0NDQ4MDB9.test');
                localStorage.setItem('refresh-token', 'a.b.c');
                localStorage.setItem('user-info', JSON.stringify({id: 'u1', username: 'research-test', roles: ['admin']}));
                localStorage.setItem('sidebar-collapsed', 'true');
            """)

            def api(route):
                request = route.request
                parsed = urlparse(request.url)
                path = unquote(parsed.path)
                if not path.startswith("/api/"):
                    return route.continue_()
                if path.startswith("/api/research/"):
                    data = request.post_data_json if request.post_data else None
                    requests.append((request.method, path, data))
                    response = backend.submit(runner.run, client.request(request.method, parsed.path + ("?" + parsed.query if parsed.query else ""), json=data)).result()
                    route.fulfill(status=response.status_code, content_type="application/json", body=response.text)
                elif path == "/api/config/llm":
                    route.fulfill(json=[dict(provider="openai", model_name="fixture", enabled=True, features=[], temperature=0.3, max_tokens=1024, timeout=60, retry_times=0)])
                elif path.endswith("/me"):
                    route.fulfill(json=dict(success=True, data=dict(id="u1", username="research-test", preferences={}, roles=["admin"])))
                else:
                    route.fulfill(json=dict(success=True, data={}))

            page.route("**/api/**", api)
            page.route_web_socket("**/api/ws/**", lambda socket: None)

            def button(label):
                return page.get_by_role("button", name=label, exact=True)

            def dialog(name):
                return page.get_by_role("dialog", name=name, exact=True)

            def title_input(surface):
                return surface.locator(".el-form-item").filter(has=page.locator("label").filter(has_text=re.compile("^标题$"))).locator("input")

            def close(surface):
                surface.locator(".el-dialog__headerbtn").click()
                expect(surface).not_to_be_visible()

            def choose(control, label):
                control.click()
                page.get_by_role("option", name=label, exact=True).click()
                page.keyboard.press("Escape")

            def shot(name):
                page.wait_for_load_state("networkidle")
                expect(page.locator(".el-loading-mask:visible")).to_have_count(0)
                if name == "review-generation-context":
                    page.locator(".context-preview").scroll_into_view_if_needed()
                page.screenshot(path=str(args.output / f"{name}-{suffix}.png"), animations="disabled")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal page overflow"
                assert page.locator(".el-dialog button, .workspace-header button").evaluate_all("""els => els.every(el => {
                    const r = el.getBoundingClientRect(); return !r.width || (r.left >= -1 && r.right <= innerWidth + 1);
                })"""), "horizontal command clipping"

            page.goto(args.base + "/research")
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(args.output / f"initial-{suffix}.png"))
            print("RECON", suffix, page.get_by_role("button").all_text_contents(), flush=True)
            button("新建复盘").click()
            current = dialog("新建复盘")
            title_input(current).fill("First holdings review")
            current.get_by_role("button", name="保存草稿", exact=True).click()
            expect(dialog("复盘")).to_be_visible()
            assert repo.db["stock_research_entries"].documents[0]["security_ids"] == ["A:600519"]
            assert repo.db["stock_research_workspaces"].documents == []
            shot("first-holdings-associations")
            close(dialog("复盘"))

            button("新建复盘").click()
            current = dialog("新建复盘")
            current.locator(".el-checkbox").filter(has_text=re.compile("^真实持仓$")).click()
            title_input(current).fill("Market only review")
            current.get_by_role("button", name="保存草稿", exact=True).click()
            current = dialog("复盘")
            market_review = next(item for item in repo.db["stock_research_entries"].documents if item["title"] == "Market only review")
            assert market_review["security_ids"] == []
            current.get_by_role("button", name="归档", exact=True).click()
            expect(current).not_to_be_visible()
            choose(page.locator(".portfolio-reviews .el-select"), "已归档")
            page.locator(".entry-row").filter(has_text="Market only review").click()
            current = dialog("复盘")
            current.get_by_role("button", name="移入回收站", exact=True).click()
            expect(current).not_to_be_visible()
            button("回收站").click()
            current = dialog("研究回收站")
            expect(current.get_by_text("Market only review · 组合", exact=True)).to_be_visible()
            shot("global-trash-market-only")
            current.get_by_role("button", name="恢复", exact=True).click()
            expect(current.get_by_text("回收站为空", exact=True)).to_be_visible()
            close(current)
            page.locator(".entry-row").filter(has_text="Market only review").click()
            dialog("复盘").get_by_role("button", name="移入回收站", exact=True).click()
            button("回收站").click()
            dialog("研究回收站").get_by_role("button", name="永久删除", exact=True).click()
            page.locator(".el-message-box").get_by_role("button", name="永久删除", exact=True).click()
            expect(dialog("研究回收站").get_by_text("回收站为空", exact=True)).to_be_visible()
            close(dialog("研究回收站"))

            call(service.get_or_create_workspace("u1", "CN", "600519", "贵州茅台"))
            call(service.save_thesis_draft("u1", "A:600519", ThesisPatch(body="Snapshot thesis")))
            page.goto(args.base + "/research/600519?market=CN")
            page.wait_for_load_state("networkidle")
            for kind in ("笔记", "调研"):
                button("新建").click()
                page.get_by_role("menuitem", name=kind, exact=True).click()
                creation = dialog("新建" + kind)
                title_input(creation).fill(kind + " manual")
                creation.locator("textarea").fill(kind + " v1")
                creation.get_by_role("button", name="创建", exact=True).click()
                expect(creation).not_to_be_visible()
                button("保存版本").click()
                page.locator(".el-message-box input").fill("Checkpoint " + kind)
                page.locator(".el-message-box").get_by_role("button", name="保存", exact=True).click()
                page.wait_for_load_state("networkidle")
                note = next(item for item in repo.db["stock_research_entries"].documents if item["title"] == kind + " manual")
                assert note["current_revision"] == 1
                page.locator(".document-fields .markdown-editor textarea").fill(kind + " v2")
                button("版本历史").click()
                versions = dialog("版本历史")
                expect(versions.get_by_text("Checkpoint " + kind, exact=True)).to_be_visible()
                shot("manual-version-" + ("note" if kind == "笔记" else "research"))
                versions.get_by_role("button", name="恢复为新版本", exact=True).click()
                page.locator(".el-message-box").get_by_role("button", name="恢复", exact=True).click()
                expect(versions.get_by_text("版本 2", exact=True)).to_be_visible()
                close(versions)
                stored = call(service.get_entry("u1", note["id"]))
                assert stored.body == kind + " v1"

            decision = call(service.create_entry("u1", NewEntry(
                entry_type="decision", scope="stock", security_id="A:600519",
                security_ids=("A:600519",), title="Linked draft decision",
                body="Immutable decision", decision_action="buy", decision_date=date(2026, 9, 8),
                references=(Reference.real_trade("real-u1", "Existing real"), Reference.paper_trade("paper-one", "Existing paper")),
            )))
            page.reload()
            page.wait_for_load_state("networkidle")
            page.get_by_role("navigation", name="研究章节").get_by_role("button", name="决策", exact=True).click()
            page.locator(".entry-row").filter(has_text="Linked draft decision").click()
            button("确认决策").click()
            dialog("确认决策").get_by_role("button", name="确认并记录", exact=True).click()
            expect(page.locator(".decision-editor .markdown-editor textarea")).to_have_count(0)
            expect(page.locator(".linked-trade")).to_have_count(2)
            button("保存成交关联").click()
            expect(page.locator(".linked-trade")).to_have_count(2)
            shot("confirmed-decision-links")
            stored = call(service.get_entry("u1", decision.id))
            assert {item.kind for item in stored.references if item.account_type in {"real", "paper"}} == {"real_trade", "paper_trade"}

            button("新建").click()
            page.get_by_role("menuitem", name="决策复盘", exact=True).click()
            current = page.locator(".review-editor")
            expect(current.get_by_role("checkbox", name="真实持仓", exact=True)).to_be_checked()
            expect(current.get_by_role("checkbox", name="模拟持仓", exact=True)).not_to_be_checked()
            choose(current.locator(".el-form-item").filter(has_text="历史决策").locator(".el-select"), "2026-09-08 · Linked draft decision")
            expect(current.get_by_role("checkbox", name="模拟持仓", exact=True)).to_be_checked()
            title_input(current).fill("Decision review defaults")
            current.get_by_role("button", name="保存草稿", exact=True).click()
            stored_review = next(item for item in repo.db["stock_research_entries"].documents if item["title"] == "Decision review defaults")
            assert "include_real_holdings" not in stored_review["scope_metadata"]
            assert "include_paper_holdings" not in stored_review["scope_metadata"]
            shot("decision-review-defaults")

            button("新建").click()
            page.get_by_role("menuitem", name="例行复盘", exact=True).click()
            button("保存草稿").click()
            button("AI 生成草稿").click()
            current = dialog("AI 生成草稿")
            expect(current.get_by_text("市场概况：资料不可用", exact=True)).to_be_visible()
            expect(current.get_by_role("checkbox", name="当前论点", exact=True)).to_be_checked()
            expect(current.get_by_text(re.compile("近期记录：.*manual"))).to_be_visible()
            current.locator(".el-checkbox").filter(has_text=re.compile("^当前论点$")).click()
            expect(current.get_by_role("checkbox", name="当前论点", exact=True)).not_to_be_checked()
            shot("review-generation-context")
            current.get_by_role("button", name="关闭", exact=True).click()

            assert not errors, errors
            assert not console_errors, console_errors
            result = dict(viewport=viewport, page_errors=errors, console_errors=console_errors, api_requests=len(requests), scenarios=["manual note/research version restore", "confirmed decision links", "decision-review effective defaults", "first-time holding associations", "global market-only archive/trash/restore/delete", "adjustable review AI context"])
            (args.output / f"result-{suffix}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print("PASS", json.dumps(result, ensure_ascii=False), flush=True)
            browser.close()
        call(client.aclose())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:5199")
    parser.add_argument("--chromium", default="/home/like/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome")
    parser.add_argument("--output", type=Path, default=Path("/tmp/research-final-wave"))
    parser.add_argument("--viewport", choices=("desktop", "mobile"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    viewports = [dict(width=1440, height=900), dict(width=390, height=844)]
    if args.viewport:
        viewports = [viewport for viewport in viewports if (viewport["width"] == 1440) == (args.viewport == "desktop")]
    for viewport in viewports:
        run(args, viewport)
