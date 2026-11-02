"""Minimal Markdown -> Jira ADF / Confluence storage, and ADF -> plain text.

Supports what the agent writes in comments and evidence pages: headings, paragraphs, flat bullet
and numbered lists, fenced code blocks, and inline `code`, **bold** and [links](url).
"""

import html
import re
from typing import Any

_INLINE = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def _blocks(md: str) -> list[tuple[str, Any]]:
    """Split markdown into (kind, payload) blocks: heading, para, bullets, ordered, code."""
    out: list[tuple[str, Any]] = []
    para: list[str] = []
    lines = md.replace("\r\n", "\n").split("\n")
    i = 0

    def flush() -> None:
        if para:
            out.append(("para", " ".join(s.strip() for s in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            flush()
            lang, body = line.strip()[3:].strip(), []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                body.append(lines[i])
                i += 1
            out.append(("code", (lang, "\n".join(body))))
        elif m := _HEADING.match(line):
            flush()
            out.append(("heading", (len(m.group(1)), m.group(2).strip())))
        elif (m := _BULLET.match(line)) or (m := _ORDERED.match(line)):
            flush()
            kind = "bullets" if _BULLET.match(line) else "ordered"
            if out and out[-1][0] == kind:
                out[-1][1].append(m.group(1))
            else:
                out.append((kind, [m.group(1)]))
        elif line.strip():
            para.append(line)
        else:
            flush()
        i += 1
    flush()
    return out


def _inline(text: str) -> list[tuple[str, str, str | None]]:
    """Split a line into (text, mark, href) runs; mark is '', 'code', 'strong' or 'link'."""
    runs, pos = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos : m.start()], "", None))
        if m.group(1) is not None:
            runs.append((m.group(1), "code", None))
        elif m.group(2) is not None:
            runs.append((m.group(2), "strong", None))
        else:
            runs.append((m.group(3), "link", m.group(4)))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], "", None))
    return runs


def _adf_inline(text: str) -> list[dict]:
    nodes = []
    for t, mark, href in _inline(text):
        node: dict[str, Any] = {"type": "text", "text": t}
        if mark == "link":
            node["marks"] = [{"type": "link", "attrs": {"href": href}}]
        elif mark:
            node["marks"] = [{"type": mark}]
        nodes.append(node)
    return nodes


def to_adf(md: str) -> dict:
    """Markdown -> Atlassian Document Format (Jira REST v3 bodies)."""
    content: list[dict] = []
    for kind, p in _blocks(md):
        if kind == "heading":
            content.append({"type": "heading", "attrs": {"level": p[0]}, "content": _adf_inline(p[1])})
        elif kind == "para":
            content.append({"type": "paragraph", "content": _adf_inline(p)})
        elif kind == "code":
            node: dict[str, Any] = {"type": "codeBlock", "content": [{"type": "text", "text": p[1]}] if p[1] else []}
            if p[0]:
                node["attrs"] = {"language": p[0]}
            content.append(node)
        else:
            items = [{"type": "listItem", "content": [{"type": "paragraph", "content": _adf_inline(t)}]} for t in p]
            content.append({"type": "bulletList" if kind == "bullets" else "orderedList", "content": items})
    return {"type": "doc", "version": 1, "content": content}


def _storage_inline(text: str) -> str:
    parts = []
    for t, mark, href in _inline(text):
        t = html.escape(t, quote=False)
        if mark == "link":
            parts.append(f'<a href="{html.escape(href or "")}">{t}</a>')
        elif mark:
            parts.append(f"<{mark}>{t}</{mark}>")
        else:
            parts.append(t)
    return "".join(parts)


def to_storage(md: str) -> str:
    """Markdown -> Confluence storage format (XHTML)."""
    out = []
    for kind, p in _blocks(md):
        if kind == "heading":
            out.append(f"<h{p[0]}>{_storage_inline(p[1])}</h{p[0]}>")
        elif kind == "para":
            out.append(f"<p>{_storage_inline(p)}</p>")
        elif kind == "code":
            lang = f'<ac:parameter ac:name="language">{html.escape(p[0])}</ac:parameter>' if p[0] else ""
            body = p[1].replace("]]>", "]]]]><![CDATA[>")
            out.append(f'<ac:structured-macro ac:name="code">{lang}<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body></ac:structured-macro>')
        else:
            tag = "ul" if kind == "bullets" else "ol"
            out.append(f"<{tag}>" + "".join(f"<li>{_storage_inline(t)}</li>" for t in p) + f"</{tag}>")
    return "".join(out)


def adf_to_text(node: Any) -> str:
    """ADF -> plain text, good enough for the model to read descriptions and comments."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "hardBreak":
        return "\n"
    inner = "".join(adf_to_text(c) for c in node.get("content", []))
    if node.get("type") == "listItem":
        return "- " + inner
    if node.get("type") in {"paragraph", "heading", "codeBlock", "bulletList", "orderedList", "blockquote", "rule"}:
        return inner.rstrip("\n") + "\n"
    return inner
