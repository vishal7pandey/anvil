from anvil.tools.markdown import adf_to_text, to_adf, to_storage

MD = """## Root cause
The `rtems_timer` check uses **<=** instead of <.
See [RTEMS-1](https://example.atlassian.net/browse/RTEMS-1).

- built erc32
- ran sp01

1. first
2. second

```c
if (a < b) {}
```
"""


def test_to_adf_blocks_and_marks():
    doc = to_adf(MD)
    kinds = [n["type"] for n in doc["content"]]
    assert kinds == ["heading", "paragraph", "bulletList", "orderedList", "codeBlock"]
    para = doc["content"][1]["content"]
    assert {"type": "text", "text": "rtems_timer", "marks": [{"type": "code"}]} in para
    assert {"type": "text", "text": "<=", "marks": [{"type": "strong"}]} in para
    assert any(n.get("marks") == [{"type": "link", "attrs": {"href": "https://example.atlassian.net/browse/RTEMS-1"}}] for n in para)
    assert doc["content"][4] == {"type": "codeBlock", "attrs": {"language": "c"}, "content": [{"type": "text", "text": "if (a < b) {}"}]}


def test_to_storage_escapes_and_uses_code_macro():
    xhtml = to_storage(MD)
    assert xhtml.startswith("<h2>Root cause</h2><p>The <code>rtems_timer</code> check uses <strong>&lt;=</strong> instead of &lt;.")
    assert "<ul><li>built erc32</li><li>ran sp01</li></ul><ol><li>first</li><li>second</li></ol>" in xhtml
    assert '<ac:parameter ac:name="language">c</ac:parameter><ac:plain-text-body><![CDATA[if (a < b) {}]]>' in xhtml


def test_adf_round_trip_to_text():
    assert adf_to_text(to_adf(MD)).splitlines()[:3] == [
        "Root cause",
        "The rtems_timer check uses <= instead of <. See RTEMS-1.",
        "- built erc32",
    ]
    assert adf_to_text(None) == ""
