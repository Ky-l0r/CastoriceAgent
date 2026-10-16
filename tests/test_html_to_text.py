"""_html_to_text 回归测试

搜索工具抓取网页正文后会用这个函数做粗略纯文本提取，
解析质量直接决定模型看到的内容，因此在改造前固定行为。
测试不联网，全部用内联 HTML 字符串。
"""

import pytest

from ai import _html_to_text


def test_plain_text_passes_through():
    assert _html_to_text("hello world") == "hello world"


def test_strips_tags():
    assert _html_to_text("<p>Hello <b>World</b></p>") == "Hello World"


def test_lowercase_and_uppercase_tags():
    assert _html_to_text("<DIV>Alpha<SPAN>Beta</SPAN></DIV>") == "Alpha Beta"


def test_removes_script_content():
    html = "<body>keep<script>var secret = 1;</script>tail</body>"
    result = _html_to_text(html)
    assert "secret" not in result
    assert "keep" in result and "tail" in result


def test_removes_script_content_case_insensitively():
    html = "<SCRIPT type='text/javascript'>alert('x')</SCRIPT>visible"
    assert _html_to_text(html) == "visible"


def test_removes_multiline_script_content():
    html = "<script>\nvar a = 1;\nvar b = 2;\n</script>after"
    assert _html_to_text(html) == "after"


def test_removes_style_content():
    html = "<head><style>body { color: red; }</style></head><p>text</p>"
    result = _html_to_text(html)
    assert "color" not in result
    assert result == "text"


def test_removes_html_comments():
    html = "<p>a<!-- 这是注释 -->b</p>"
    assert _html_to_text(html) == "a b"


def test_unescapes_entities():
    html = "<p>Tom &amp; Jerry &lt;3 &quot;ok&quot;</p>"
    assert _html_to_text(html) == 'Tom & Jerry <3 "ok"'


def test_nbsp_becomes_plain_space():
    assert _html_to_text("a&nbsp;b") == "a b"


def test_collapses_whitespace_and_newlines():
    html = "<div>  a\n\n   b\t\tc  </div>"
    assert _html_to_text(html) == "a b c"


def test_strips_leading_and_trailing_whitespace():
    assert _html_to_text("   <p>  x  </p>   ") == "x"


def test_truncates_to_max_len():
    assert _html_to_text("x" * 600) == "x" * 500
    assert len(_html_to_text("x" * 601, max_len=500)) == 500


def test_custom_max_len():
    assert _html_to_text("abcdefghij", max_len=4) == "abcd"


def test_max_len_zero_returns_empty():
    assert _html_to_text("<p>abc</p>", max_len=0) == ""


def test_truncation_applies_after_collapsing():
    # 折叠后的长度才算数：30 个空格 + 10 个字符，按 10 截断仍应拿到全部字符
    assert _html_to_text(" " * 30 + "<p>abcdefghij</p>", max_len=10) == "abcdefghij"


def test_empty_input():
    assert _html_to_text("") == ""


def test_tag_only_input_yields_empty():
    assert _html_to_text("<div><br/></div>") == ""


def test_result_never_contains_angle_bracket_tags():
    html = '<a href="http://example.com">link</a><img src="x.png"/>'
    result = _html_to_text(html)
    assert "<" not in result
    assert result == "link"


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<h1>标题</h1><p>正文</p>", "标题 正文"),
        ("<ul><li>一</li><li>二</li></ul>", "一 二"),
        ("<p>第一段</p>\n<p>第二段</p>", "第一段 第二段"),
        ("<html><body>只有正文</body></html>", "只有正文"),
    ],
)
def test_common_page_shapes(html, expected):
    assert _html_to_text(html) == expected


def test_bing_result_snippet_roundtrip():
    """搜索结果摘要里常见的 <strong> 高亮标签要被去掉"""
    # 标签被替换为空格（不是直接删除），因此相邻文字之间会多出一个空格
    snippet = "Python 是一种<strong>解释型</strong>语言 &amp; 很流行"
    assert _html_to_text(snippet) == "Python 是一种 解释型 语言 & 很流行"
