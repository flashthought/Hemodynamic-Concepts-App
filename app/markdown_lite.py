"""
Minimal Markdown -> HTML renderer for the Design History File viewer.

Supports the subset used in docs/: headings, paragraphs, bold/italic, inline
code, fenced code blocks, ordered/unordered lists (with nesting), GitHub-style
tables, links, images, block quotes and horizontal rules. All text is
HTML-escaped first, so document content cannot inject markup or script.
Written in-house to avoid an extra third-party (SOUP) dependency.
"""

import html
import re

_INLINE_CODE = re.compile(r"`([^`]+)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])")


def _safe_url(url: str) -> str:
    url = html.unescape(url)
    if re.match(r"^(https?:|mailto:|#|/|\.{0,2}/?[\w\-./]+$)", url) and not url.lower().startswith("javascript:"):
        return html.escape(url, quote=True)
    return "#"


def inline(text: str, base: str = "") -> str:
    text = html.escape(text, quote=False)
    codes = []

    def keep(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = _INLINE_CODE.sub(keep, text)
    text = re.sub(r"\\([*_|\\])", lambda m: f"&#{ord(m.group(1))};", text)  # Markdown backslash escapes

    def img(m):
        src = m.group(2)
        if not re.match(r"^(https?:|/)", src):
            src = base + src
        return f'<img src="{_safe_url(src)}" alt="{m.group(1)}" loading="lazy">'

    text = _IMAGE.sub(img, text)
    def link(m):
        url = m.group(2)
        # Links between Design History File documents ("04_concept_selection.md#x") open in the in-app viewer.
        doc = re.fullmatch(r"([\w\-]+)\.md(#[\w\-]*)?", url)
        if doc:
            url = f"#/design/{doc.group(1)}"
        return f'<a href="{_safe_url(url)}">{m.group(1)}</a>'

    text = _LINK.sub(link, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)


def _table(lines, base):
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    head, body = rows[0], rows[2:]
    aligns = []
    for spec in rows[1]:
        spec = spec.strip()
        aligns.append("center" if spec.startswith(":") and spec.endswith(":") else "right" if spec.endswith(":") else "")
    def cell(tag, value, i):
        style = f' style="text-align:{aligns[i]}"' if i < len(aligns) and aligns[i] else ""
        return f"<{tag}{style}>{inline(value, base)}</{tag}>"
    out = ['<div class="table-wrap"><table><thead><tr>']
    out += [cell("th", v, i) for i, v in enumerate(head)]
    out.append("</tr></thead><tbody>")
    for r in body:
        out.append("<tr>" + "".join(cell("td", v, i) for i, v in enumerate(r)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _list(lines, base):
    """Render a (possibly nested) list block from raw lines."""
    out, stack = [], []
    item_re = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")
    for ln in lines:
        m = item_re.match(ln)
        if not m:
            if out:
                out[-1] = out[-1][:-5] + " " + inline(ln.strip(), base) + "</li>"
            continue
        indent, marker, text = len(m.group(1).replace("\t", "    ")), m.group(2), m.group(3)
        tag = "ol" if marker[0].isdigit() else "ul"
        while stack and indent < stack[-1][0]:
            out.append(f"</{stack.pop()[1]}>")
        if not stack or indent > stack[-1][0]:
            stack.append((indent, tag))
            out.append(f"<{tag}>")
        out.append(f"<li>{inline(text, base)}</li>")
    while stack:
        out.append(f"</{stack.pop()[1]}>")
    return "".join(out)


def render(md: str, base: str = "") -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, para = [], 0, []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para), base) + "</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            flush()
            j = i + 1
            code = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j]); j += 1
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            i = j + 1
            continue
        if not s:
            flush(); i += 1; continue
        h = re.match(r"^(#{1,4})\s+(.*)$", s)
        if h:
            flush()
            level = len(h.group(1))
            text = h.group(2)
            anchor = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
            out.append(f'<h{level} id="{anchor}">{inline(text, base)}</h{level}>')
            i += 1; continue
        if re.match(r"^(-{3,}|\*{3,})$", s):
            flush(); out.append("<hr>"); i += 1; continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{3,}", lines[i + 1].strip()):
            flush()
            j = i
            block = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                block.append(lines[j]); j += 1
            out.append(_table(block, base))
            i = j; continue
        if re.match(r"^([-*]|\d+\.)\s+", s):
            flush()
            j = i
            block = []
            while j < len(lines) and (re.match(r"^\s*([-*]|\d+\.)\s+", lines[j]) or
                                      (lines[j].startswith("  ") and lines[j].strip() and block)):
                block.append(lines[j]); j += 1
            out.append(_list(block, base))
            i = j; continue
        if s.startswith(">"):
            flush()
            j = i
            quote = []
            while j < len(lines) and lines[j].strip().startswith(">"):
                quote.append(lines[j].strip()[1:].strip()); j += 1
            out.append("<blockquote>" + inline(" ".join(quote), base) + "</blockquote>")
            i = j; continue
        para.append(s)
        i += 1
    flush()
    return "\n".join(out)
