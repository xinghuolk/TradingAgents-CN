"""Fixture-only generation reopen regression; run against a local Vite server."""

import argparse
import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import expect, sync_playwright


def run(args, viewport):
    suffix = "desktop" if viewport["width"] == 1440 else "mobile"
    stamp = "2026-09-08T00:00:00Z"
    workspace = dict(
        security_id="A:600519", market="CN", code="600519", name="贵州茅台",
        body="人工论点", assumptions=[], risks=[], invalidation_conditions=[],
        open_questions=[], tags=[], external_links=[], current_revision=0,
        created_at=stamp, updated_at=stamp,
    )
    references = [
        dict(kind="real_trade", source_id="trade-1", account_type="real",
             source_date="2026-09-08", label="买入 100 股", available=True,
             security_id="A:600519", snapshot={}),
        dict(kind="analysis_report", source_id="report-1", account_type=None,
             source_date="2026-09-08", label="公司分析", available=True,
             security_id="A:600519", snapshot={}),
    ]
    entry, tasks, submitted, errors, console_errors, polls = {}, {}, [], [], [], []
    modes = dict(status="pending", enabled=True, reasoning=True)

    with sync_playwright() as playwright:
        launch = dict(headless=True, args=["--no-sandbox"])
        if args.chromium:
            launch["executable_path"] = args.chromium
        browser = playwright.chromium.launch(**launch)
        page = browser.new_page(viewport=viewport)
        page.set_default_timeout(10000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: console_errors.append(message.text)
                if message.type == "error" else None)
        page.add_init_script("""
            localStorage.setItem('auth-token', 'eyJhbGciOiJub25lIn0.eyJleHAiOjQxMDI0NDQ4MDB9.test');
            localStorage.setItem('refresh-token', 'a.b.c');
            localStorage.setItem('user-info', JSON.stringify({id: 'test', username: 'research-test', roles: ['admin']}));
            localStorage.setItem('sidebar-collapsed', 'true');
        """)

        def api(route):
            path = unquote(urlparse(route.request.url).path)
            if not path.startswith("/api/"):
                return route.continue_()
            method = route.request.method
            data = route.request.post_data_json if route.request.post_data else {}
            if path == "/api/config/llm":
                return route.fulfill(json=[
                    dict(provider="openai", model_name="reasoning-fixture", enabled=modes["enabled"],
                         features=["reasoning"] if modes["reasoning"] else [], max_tokens=1024,
                         temperature=0.3, timeout=60, retry_times=0),
                    dict(provider="codex", model_name="gpt-fixture", enabled=True,
                         features=["reasoning"], max_tokens=1024, temperature=0.3, timeout=60, retry_times=0),
                ])
            if path == "/api/research/workspaces":
                result = workspace if method == "POST" else dict(items=[workspace], total=1, page=1, page_size=20)
            elif path.startswith("/api/research/workspaces/"):
                result = workspace
            elif path == "/api/research/entries" and method == "POST":
                entry.update(dict(
                    id="entry-1", entry_type="decision", scope="stock", security_id="A:600519",
                    security_ids=[], title="", body="", status="draft", references=[], tags=[],
                    external_links=[], topic=None, conclusion=None, decision_action=None,
                    decision_date=None, planned_price=None, target_allocation=None, horizon=None,
                    thesis_snapshot=None, review_kind=None, decision_id=None, scope_metadata={},
                    ai_drafts=[], source_metadata={}, warnings=[], current_revision=0,
                    confirmed_at=None, archived_at=None, deleted_at=None, created_at=stamp, updated_at=stamp,
                ))
                entry.update(data)
                result = entry
            elif path == "/api/research/entries":
                result = dict(items=[entry] if entry else [], total=int(bool(entry)), page=1, page_size=20)
            elif path.endswith("/references") or path.endswith("/recommendations"):
                result = references
            elif path.startswith("/api/research/entries/"):
                if method == "PATCH":
                    entry.update(data)
                result = entry
            elif path == "/api/research/generation-tasks":
                assert set(data) == {"target_entry_id", "draft_kind", "provider", "model_name", "reasoning_effort", "references"}
                submitted.append(deepcopy(data))
                modes["status"] = "pending"
                result = dict(
                    id=f"task-{len(submitted)}", status="pending", prompt_version="research-draft-v1",
                    source_ids=[item["source_id"] for item in data["references"]], generated_at=None,
                    content=None, error_message=None, **data,
                )
                tasks[result["id"]] = result
            elif path.startswith("/api/research/generation-tasks/"):
                identifier = path.split("/")[-1]
                polls.append(identifier)
                result = tasks[identifier]
                result.update(status=modes["status"], error_message="Fixture generation failed"
                              if modes["status"] == "failed" else None)
            elif path.endswith("/me"):
                result = dict(id="test", username="research-test", preferences={}, roles=["admin"])
            else:
                result = {}
            route.fulfill(json=dict(success=True, data=result))

        page.route("**/api/**", api)
        page.route_web_socket("**/api/ws/**", lambda socket: None)

        def dialog():
            return page.get_by_role("dialog", name="AI 生成草稿", exact=True)

        def button(label):
            return page.get_by_role("button", name=label, exact=True)

        def option(control, label):
            single = control.get_attribute("role") == "combobox"
            if single:
                control = control.locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " el-select ")][1]')
            control.click()
            page.get_by_role("option", name=label, exact=True).first.click()
            if not single:
                page.keyboard.press("Escape")
            page.wait_for_timeout(350)

        def close():
            dialog().get_by_role("button", name="关闭", exact=True).click()
            expect(dialog()).to_have_count(0)

        def reopen():
            page.evaluate("""localStorage.setItem('research-generation:test', JSON.stringify({
                provider: 'codex', model_name: 'gpt-fixture', reasoning_effort: null
            }))""")
            with page.expect_response("**/api/config/llm"):
                button("AI 生成草稿").click()

        def restored(active=False, model_label="reasoning-fixture", effort_label="高"):
            for label in ["openai", model_label, effort_label, "真实账户 · 买入 100 股", "分析报告 · 公司分析"]:
                expect(dialog().get_by_text(label, exact=True)).to_be_visible()
            for label in ["提供商", "模型", "推理强度"]:
                control = dialog().get_by_role("combobox", name=label, exact=True)
                if active:
                    expect(control).to_be_disabled()
                else:
                    expect(control).to_be_enabled()
            reference_input = dialog().locator(".reference-picker input")
            if active:
                expect(reference_input).to_be_disabled()
            else:
                expect(reference_input).to_be_enabled()

        def shot(name):
            page.screenshot(path=str(args.output / f"{name}-{suffix}.png"), animations="disabled")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert dialog().locator("button").evaluate_all("""elements => elements.every(element => {
                const bounds = element.getBoundingClientRect();
                return !bounds.width || (bounds.left >= 0 && bounds.right <= innerWidth + 1);
            })"""), "dialog command is horizontally clipped"

        page.goto(args.base + "/research/600519?market=CN")
        page.wait_for_load_state("networkidle")
        print("RECON", suffix, page.get_by_role("button").all_text_contents(), flush=True)
        button("新建").click()
        page.get_by_role("menuitem", name="决策", exact=True).click()
        button("保存草稿").click()
        expect(button("AI 生成草稿")).to_be_visible()
        page.get_by_role("textbox", name="决策正文", exact=True).fill("Human body remains unchanged")
        entry_picker = page.locator(".decision-editor .reference-picker")
        option(entry_picker.locator(".el-select"), "真实账户 · 买入 100 股")
        height = entry_picker.bounding_box()["height"]
        button("AI 生成草稿").click()
        expect(dialog().get_by_role("button", name="生成草稿", exact=True)).to_be_disabled()
        assert abs(entry_picker.bounding_box()["height"] - height) < 1, "reference picker moves the AI button on first click"
        option(dialog().get_by_role("combobox", name="提供商", exact=True), "openai")
        option(dialog().get_by_role("combobox", name="模型", exact=True), "reasoning-fixture")
        option(dialog().get_by_role("combobox", name="推理强度", exact=True), "高")
        option(dialog().locator(".reference-picker .el-select"), "分析报告 · 公司分析")
        dialog().get_by_role("button", name="生成草稿", exact=True).click()
        expect(dialog().get_by_text("等待生成", exact=True)).to_be_visible()
        expected = dict(
            target_entry_id="entry-1", draft_kind="decision", provider="openai",
            model_name="reasoning-fixture", reasoning_effort="high", references=[
                dict(kind="real_trade", source_id="trade-1", account_type="real", source_date="2026-09-08", label="买入 100 股"),
                dict(kind="analysis_report", source_id="report-1", account_type=None, source_date="2026-09-08", label="公司分析"),
            ],
        )
        assert submitted == [expected]
        assert [item["source_id"] for item in entry["references"]] == ["trade-1"]
        close()
        count = len(polls)
        page.wait_for_timeout(2200)
        assert len(polls) == count, "closed dialog keeps polling"
        reopen()
        restored(active=True)
        expect(dialog().get_by_text("等待生成", exact=True)).to_be_visible()
        shot("frozen-pending")
        modes["status"] = "running"
        expect(dialog().get_by_text("正在生成", exact=True)).to_be_visible()
        close()
        reopen()
        restored(active=True)
        expect(dialog().get_by_text("正在生成", exact=True)).to_be_visible()
        modes["status"] = "failed"
        expect(dialog().get_by_text("生成失败", exact=True)).to_be_visible()
        close()
        reopen()
        restored()
        expect(dialog().get_by_text("生成失败", exact=True)).to_be_visible()
        shot("frozen-failed")
        dialog().get_by_role("button", name="重新生成", exact=True).click()
        expect(dialog().get_by_text("等待生成", exact=True)).to_be_visible()
        assert submitted == [expected, expected], "retry changed frozen model, effort, or references"
        modes["status"] = "failed"
        expect(dialog().get_by_text("生成失败", exact=True)).to_be_visible()
        close()
        modes["enabled"] = False
        reopen()
        restored(model_label="reasoning-fixture（不可用）", effort_label="high（不再支持）")
        expect(dialog().get_by_role("alert").filter(has_text="所选模型已不可用，请重新选择模型")).to_be_visible()
        expect(dialog().get_by_role("button", name="重新生成", exact=True)).to_be_disabled()
        shot("unavailable-model")
        assert len(submitted) == 2
        close()
        modes.update(enabled=True, reasoning=False)
        reopen()
        restored(effort_label="high（不再支持）")
        expect(dialog().get_by_role("alert").filter(has_text="所选推理强度已不受当前模型支持，请重新选择推理强度")).to_be_visible()
        expect(dialog().get_by_role("button", name="重新生成", exact=True)).to_be_disabled()
        option(dialog().get_by_role("combobox", name="推理强度", exact=True), "当前模型不支持单独设置")
        dialog().get_by_role("button", name="重新生成", exact=True).click()
        expect(dialog().get_by_text("等待生成", exact=True)).to_be_visible()
        assert submitted == [expected, expected, dict(expected, reasoning_effort=None)]
        close()
        expect(page.get_by_role("textbox", name="决策正文", exact=True)).to_have_value("Human body remains unchanged")
        assert not errors, errors
        assert not console_errors, console_errors
        result = dict(viewport=viewport, task_count=len(submitted), page_errors=errors,
                      console_errors=console_errors, exact_retry=True)
        (args.output / f"result-{suffix}.json").write_text(json.dumps(result, indent=2))
        print("PASS", json.dumps(result), flush=True)
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:5199")
    parser.add_argument("--chromium", help="Optional existing Chromium executable")
    parser.add_argument("--output", type=Path, default=Path("/tmp/research-generation-reopen"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for viewport in [dict(width=1440, height=900), dict(width=390, height=844)]:
        run(args, viewport)
