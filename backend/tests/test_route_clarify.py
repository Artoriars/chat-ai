"""M3 clarify 与结论路由单测：JSON 解析降级、模板兜底（不依赖 LLM）。"""
from app.graph.nodes import _parse_clarify_json


def test_parse_clarify_valid():
    content = '{"clarify_question": "想看哪个时间范围？", "options": ["近7天", "近30天", "本月"]}'
    parsed = _parse_clarify_json(content, "fallback")
    assert parsed["question"] == "想看哪个时间范围？"
    assert parsed["options"] == ["近7天", "近30天", "本月"]


def test_parse_clarify_options_capped_at_4():
    content = '{"clarify_question": "q", "options": ["1", "2", "3", "4", "5", "6"]}'
    assert len(_parse_clarify_json(content, "fallback")["options"]) == 4


def test_parse_clarify_garbage_falls_back():
    parsed = _parse_clarify_json("这不是 JSON", "模板追问")
    assert parsed == {"question": "模板追问", "options": []}

    # JSON 里缺字段也走兜底
    parsed2 = _parse_clarify_json('{"options": ["a"]}', "模板追问2")
    assert parsed2["question"] == "模板追问2"


def test_parse_clarify_broken_json_falls_back():
    parsed = _parse_clarify_json('{"clarify_question": "截断', "模板追问")
    assert parsed == {"question": "模板追问", "options": []}
