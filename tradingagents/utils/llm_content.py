"""
LLM 输出内容规范化工具

背景：部分模型（尤其是 Gemini / langchain-google-genai）可能返回多段 content parts，
例如: [{"type": "text", "text": "..."}]，而项目中大量逻辑假设 content 为 str，
会导致 `.strip()` / `re.search()` / 前端渲染出现异常或显示为原始结构。
"""

from __future__ import annotations

import json
from typing import Any


def coerce_llm_content_to_text(content: Any) -> str:
    """
    将 LangChain message.content（可能是 str / list(parts) / dict / 其他对象）转换为纯文本。

    - str: 原样返回
    - list: 提取其中的文本片段并用换行拼接
    - dict: 优先取 text/content 字段，否则 JSON 序列化
    - 其他: str() 转换
    """

    if content is None:
        return ""

    if isinstance(content, str):
        return content

    # Gemini 常见格式：list[dict(type="text", text="...")]
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if item is None:
                continue
            if isinstance(item, str):
                if item:
                    parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
                    continue
                inner = item.get("content")
                if isinstance(inner, str) and inner:
                    parts.append(inner)
                    continue
                # 忽略非文本 part（如 image/tool 等）
                continue
            # 兜底：尽量保留信息
            s = str(item)
            if s:
                parts.append(s)
        return "\n".join(parts).strip()

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        inner = content.get("content")
        if isinstance(inner, str):
            return inner
        return json.dumps(content, ensure_ascii=False)

    return str(content)


def safe_preview(content: Any, limit: int = 300) -> str:
    """生成安全的内容预览（用于日志），避免 list/dict 导致切片异常。"""
    text = coerce_llm_content_to_text(content)
    if len(text) > limit:
        return text[:limit] + "..."
    return text

