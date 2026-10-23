import asyncio
import json

import httpx
import pytest

from app import extract, normalize, turbo_stream
from tests.helpers import fixtures as f

GOOD = f"https://chatgpt.com/share/{f.SHARE_ID}"


@pytest.mark.parametrize("url", [
    GOOD, GOOD + "/", GOOD + "?utm=x#frag", f"https://chat.openai.com/share/{f.SHARE_ID}",
    f"https://CHATGPT.com/share/{f.SHARE_ID.upper()}", f"  {GOOD}  ",
])
def test_accepts_share_links(url):
    canonical, share_id = extract.validate_share_url(url)
    assert canonical == GOOD and share_id == f.SHARE_ID


@pytest.mark.parametrize("url", [
    None, "", "chatgpt.com/share/" + f.SHARE_ID, f"http://chatgpt.com/share/{f.SHARE_ID}",
    f"https://chatgpt.com.evil.example/share/{f.SHARE_ID}", f"https://evil.example/share/{f.SHARE_ID}",
    f"https://user:pw@chatgpt.com/share/{f.SHARE_ID}", f"https://chatgpt.com:8443/share/{f.SHARE_ID}",
    f"https://chatgpt.com/c/{f.SHARE_ID}", "https://chatgpt.com/share/not-a-uuid", "https://chatgpt.com/share/../../admin",
    "https://169.254.169.254/latest/meta-data", "file:///etc/passwd", "https://chatgpt.com/share/" + "a" * 400,
])
def test_rejects_other_urls(url):
    with pytest.raises(extract.ExtractionError) as e:
        extract.validate_share_url(url)
    assert e.value.code == "invalid_url"


def run(coro):
    return asyncio.run(coro)


def transport(handler):
    return httpx.MockTransport(handler)


def test_fetch_follows_only_allowed_redirects():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        if req.url.host == "chat.openai.com":
            return httpx.Response(301, headers={"location": GOOD})
        return httpx.Response(200, text="<html>ok</html>")

    assert run(extract.fetch_page(f"https://chat.openai.com/share/{f.SHARE_ID}", transport=transport(handler))) == "<html>ok</html>"
    assert calls[-1] == GOOD


@pytest.mark.parametrize("location", ["http://169.254.169.254/", "https://evil.example/x", "http://chatgpt.com/share/x",
                                      "https://chatgpt.com:9000/share/x", "file:///etc/passwd"])
def test_fetch_blocks_redirects_off_chatgpt(location):
    seen = []

    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(302, headers={"location": location})

    with pytest.raises(extract.ExtractionError) as e:
        run(extract.fetch_page(GOOD, transport=transport(handler)))
    assert e.value.code == "blocked_redirect"
    assert seen == ["chatgpt.com"]  # never requested the redirect target


@pytest.mark.parametrize("status,code", [(404, "not_found"), (410, "not_found"), (403, "inaccessible"),
                                         (429, "inaccessible"), (500, "unavailable")])
def test_fetch_status_errors(status, code):
    with pytest.raises(extract.ExtractionError) as e:
        run(extract.fetch_page(GOOD, transport=transport(lambda r: httpx.Response(status))))
    assert e.value.code == code


def test_fetch_size_limit(monkeypatch):
    monkeypatch.setattr(extract, "MAX_BYTES", 1000)
    with pytest.raises(extract.ExtractionError) as e:
        run(extract.fetch_page(GOOD, transport=transport(lambda r: httpx.Response(200, content=b"x" * 5000))))
    assert e.value.code == "too_large"


def test_redirect_loop_stops():
    with pytest.raises(extract.ExtractionError):
        run(extract.fetch_page(GOOD, transport=transport(lambda r: httpx.Response(302, headers={"location": GOOD}))))


@pytest.mark.parametrize("page", [f.next_data_page(), f.turbo_page(), f.turbo_page(split=1), f.turbo_page(split=40)])
def test_structured_formats_preserve_text_and_order(page):
    r = extract.parse_structured(page, f.SHARE_ID)
    assert r.method == "structured"
    assert r.title == "Email to my manager (SYNTHETIC)"
    assert [(m["role"], m["text"]) for m in r.messages] == f.UNICODE_TURNS   # exact, untrimmed, in order
    assert {w["code"] for w in r.warnings} == {"branches_omitted"}
    assert r.complete


def test_whitespace_and_formatting_preserved():
    turns = [("user", "  leading and trailing  \n\n\tindented\r\nline"), ("assistant", "```py\nprint('x')\n```\n**bold** _it_")]
    r = extract.parse_structured(f.next_data_page(f.conversation(turns)), "s")
    assert [m["text"] for m in r.messages] == [t for _, t in turns]


def test_skips_hidden_system_tool_and_empty_messages():
    conv = f.conversation([("user", "hi"), ("assistant", "hello")], with_branch=False)
    leaf = conv["current_node"]
    extra = {
        "tool": {"author": {"role": "tool"}, "content": {"content_type": "text", "parts": ["search results"]}},
        "call": {"author": {"role": "assistant"}, "recipient": "browser", "content": {"content_type": "code", "text": "search()"}},
        "empty": {"author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["   "]}},
        "think": {"author": {"role": "assistant"}, "content": {"content_type": "thoughts", "thoughts": []}},
        "final": {"author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["done"]}},
    }
    for nid, msg in extra.items():
        conv["mapping"][leaf]["children"].append(nid)
        conv["mapping"][nid] = {"id": nid, "parent": leaf, "children": [], "message": {"id": nid, **msg}}
        leaf = nid
    conv["current_node"] = leaf
    r = extract.parse_structured(f.next_data_page(conv), "s")
    assert [m["text"] for m in r.messages] == ["hi", "hello", "done"]
    assert r.complete


def test_images_get_placeholder_and_warning():
    conv = f.conversation([("user", "x"), ("assistant", "y")], with_branch=False)
    nid = conv["current_node"]
    conv["mapping"][nid]["message"]["content"] = {"content_type": "multimodal_text",
                                                  "parts": [{"content_type": "image_asset_pointer"}, "what is this?"]}
    r = extract.parse_structured(f.next_data_page(conv), "s")
    assert r.messages[-1]["text"] == "[image]\nwhat is this?"
    assert "images_omitted" in {w["code"] for w in r.warnings}


def test_broken_chain_is_flagged_as_uncertain():
    conv = f.conversation([("user", "a"), ("assistant", "b"), ("user", "c")], with_branch=False)
    mid = [k for k, v in conv["mapping"].items() if (v.get("message") or {}).get("content", {}).get("parts") == ["b"]][0]
    del conv["mapping"][mid]
    r = extract.parse_structured(f.next_data_page(conv), "s")
    assert not r.complete and "broken_chain" in {w["code"] for w in r.warnings}


def test_no_conversation_data_returns_none():
    assert extract.parse_structured("<html><script>var x = 1</script></html>", "s") is None
    assert extract.parse_structured(f.rendered_page(), "s") is None


def test_extract_end_to_end_and_errors(monkeypatch):
    async def page(_url, transport=None):
        return pages.pop(0)

    monkeypatch.setattr(extract, "fetch_page", page)
    pages = [f.turbo_page()]
    r = run(extract.extract(GOOD))
    assert len(r.messages) == 4
    pages = ["<html></html>"]
    with pytest.raises(extract.ExtractionError) as e:
        run(extract.extract(GOOD))
    assert e.value.code == "extraction_failed"
    pages = [f.next_data_page(f.conversation([("user", "only me")], with_branch=False))]
    r = run(extract.extract(GOOD))
    assert not r.complete and "one_sided" in {w["code"] for w in r.warnings}


def test_turbo_stream_constants_and_cycles():
    assert turbo_stream.decode([{"_1": -5, "_2": -7}, "a", "b"]) == {"a": None, "b": None}
    cyc = turbo_stream.decode([{"_1": 0}, "self"])
    assert cyc["self"] is cyc
    assert turbo_stream.decode("not a list") is None
    assert turbo_stream.decode_stream('garbage\n[{"_1":2},"k","v"]\nP3:[{"_1":2},"x","y"]') == [{"k": "v"}, {"x": "y"}]


def test_normalize_ids_and_exclusion():
    msgs = [{"source_id": f"id-{i}", "role": r, "text": t} for i, (r, t) in enumerate(f.UNICODE_TURNS)]
    d = normalize.build_dialogue("c1", msgs, keep_indexes=[0, 2, 3])
    assert [m["message_id"] for m in d] == ["id-0", "id-2", "id-3"]
    assert [m["index"] for m in d] == [0, 1, 2] and [m["source_index"] for m in d] == [0, 2, 3]
    assert [m["author"] for m in d] == ["You", "You", "ChatGPT"]
    normalize.validate_dialogue(d, msgs)
    bad = [dict(m, source_id="dup") for m in msgs]
    d2 = normalize.build_dialogue("c9", bad)
    assert [m["message_id"] for m in d2] == ["c9_m0000", "c9_m0001", "c9_m0002", "c9_m0003"]
    assert normalize.build_dialogue("c9", bad) == d2  # deterministic


@pytest.mark.parametrize("mutate", [
    lambda d: d[0].update(text=d[0]["text"] + " "),
    lambda d: d[0].update(text=d[0]["text"].replace("🙂", ":)")),
    lambda d: d[1].update(index=5),
    lambda d: d[1].update(message_id=d[0]["message_id"]),
    lambda d: d[0].update(author="ChatGPT"),
    lambda d: d.reverse(),
])
def test_validate_dialogue_catches_changes(mutate):
    msgs = [{"source_id": f"id-{i}", "role": r, "text": t} for i, (r, t) in enumerate(f.UNICODE_TURNS)]
    d = normalize.build_dialogue("c1", msgs)
    mutate(d)
    with pytest.raises(normalize.NormalizationError):
        normalize.validate_dialogue(d, msgs)


def test_task_data_is_json_and_has_dialogue():
    msgs = [{"source_id": "a", "role": "user", "text": "hi"}, {"source_id": "b", "role": "assistant", "text": "yo"}]
    data = normalize.task_data("conv_1", "P01", "t", normalize.build_dialogue("c", msgs), "1.0")
    assert json.loads(json.dumps(data))["dialogue"][1] == {"message_id": "b", "index": 1, "role": "assistant", "author": "ChatGPT", "text": "yo"}
