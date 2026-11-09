"""Offline harness regressions. Run: python -B tools/check_harness_selections.py.

Only styles.json is loaded as fixture data. No Workbench startup, credentials,
external HTTP, real messages, generation, or job writes are permitted.
"""
from __future__ import annotations

import copy
import json
import socket
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "workbench"))

from skillbridge import agent, api, harness  # noqa: E402
from skillbridge.harness_context import (  # noqa: E402
    batch_context, batch_prompt, describe_context, job_selections, validate_selections,
)
from skillbridge.skill import ColorRef, GarmentType, Skill  # noqa: E402

TEN_COLORS = ["black", "white", "pink", "apricot", "grey", "red",
              "heather-grey", "brown", "blue", "forest-green"]


def catalog_skill():
    """Read the real public style catalog, never config/.env or production jobs."""
    rows = json.loads((ROOT / "workbench/library/styles.json").read_text(encoding="utf-8"))
    types = {}
    for row in rows:
        if row["slug"] not in {"23-sorona-tee", "07-washed-tee"}:
            continue
        refs = []
        for color in row["colors"]:
            hue = color["hex"].lstrip("#")
            refs.append(ColorRef(
                label=color["label"], name_zh=color["name_zh"], code=color["color_code"],
                hex=color["hex"], rgb=tuple(int(hue[i:i + 2], 16) for i in (0, 2, 4)),
                path=ROOT / "workbench/library" / color["image"],
            ))
        types[row["slug"]] = GarmentType(
            slug=row["slug"], aliases=row["aliases"], ref_dir=ROOT / "workbench/library",
            phrase=row["phrase"], colors=refs, name_en=row["name_en"], fit=row.get("fit", ""),
        )
    return Skill(root=ROOT, types=types, prompt_template="", negative_prompt="", max_bytes=20_000_000)


class FakePricer:
    """Explicit fixture prices, not a claim about current production pricing."""
    def __init__(self):
        self.calls = []

    def estimate(self, model, count, size=None, provider=""):
        self.calls.append((provider, model, count, size))
        unit = {("mock-a", "mock-image", "1536x1024"): 0.065,
                ("mock-b", "mock-image", "1536x1024"): 0.1}.get((provider, model, size))
        return {"model": model, "count": count, "size": size, "unit": unit,
                "total": None if unit is None else round(unit * count, 4),
                "currency": "CNY", "priced": unit is not None, "tier": "fixture"}


class FakeBench:
    def __init__(self, sources=1, online=True):
        self.skill = catalog_skill()
        self.pricer = FakePricer()
        self.env = {"OFFLINE_HARNESS_KEY": "offline-sentinel-not-a-real-key"}
        self.sources = [Path(f"mock-source-{i}.jpg") for i in range(sources)]
        self.online = online
        self.created = []
        self.reworked = []
        self.started = []
        self.source_job = None

    def defaults(self):
        return {"output_format": "auto", "harness": {
            "enabled": self.online, "api_key_env": "OFFLINE_HARNESS_KEY",
            "model": "mock-deepseek", "base_url": "https://offline.invalid/v1", "max_steps": 5,
        }}

    def get_provider(self, name=None):
        if name not in {None, "mock-a", "mock-b"}:
            raise AssertionError("unexpected provider")
        return SimpleNamespace(name=name or "mock-a", cfg={"model": "mock-image"})

    def draft_image_paths(self, draft_id):
        assert draft_id == "offline-draft"
        return self.sources

    def draft_uploads(self, draft_id):
        assert draft_id == "offline-draft"
        return [(path.name, b"mock-only-not-an-image") for path in self.sources]

    def create_job(self, uploads, **kwargs):
        self.created.append(copy.deepcopy(kwargs))
        count = len(uploads) * sum(len(row["colors"]) for row in kwargs["selections"])
        return SimpleNamespace(id="mock-created", tasks=[None] * count,
                               params={"concurrency": kwargs["concurrency"], "estimate": {
                                   "priced": False, "unit": None, "total": None}})

    async def create_rework_job(self, job_id, task_ids, text, **kwargs):
        self.reworked.append((job_id, list(task_ids), text, copy.deepcopy(kwargs)))
        return SimpleNamespace(id="mock-rework", tasks=[None] * len(task_ids), params={})

    def get_job(self, job_id):
        assert job_id == "20261107-112438-4858"
        return self.source_job

    def start(self, job_id):
        self.started.append(job_id)


class ChatScript:
    """In-memory DeepSeek streaming fixture; never opens a connection."""
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def client(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, *, json, headers):
        assert url == "https://offline.invalid/v1/chat/completions"
        self.requests.append(copy.deepcopy(json))
        if not self.turns:
            raise AssertionError("mock chat exhausted")
        delta = self.turns.pop(0)
        return MockResponse(delta)


class MockResponse:
    status_code = 200

    def __init__(self, delta):
        self.delta = delta

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        yield "data: " + json.dumps({"choices": [{"delta": self.delta}]}, ensure_ascii=False)
        yield "data: [DONE]"


def tool_turn(name, args, index=0):
    return {"tool_calls": [{"index": index, "id": f"mock-call-{index}",
                             "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


def intake_payload(**updates):
    payload = {"mode": "intake", "draft_id": "offline-draft", "count": 9,
               "garment_type": "23-sorona-tee", "colors": ["black"],
               "selections": [{"garment_type": "23-sorona-tee", "garment_name": "untrusted-name",
                               "colors": list(TEN_COLORS)}],
               "provider": "mock-a", "model": "mock-image", "size": "1536x1024",
               "quality": "high", "output_format": "webp", "subject": "person",
               "pattern_mode": "preserve", "send_reference": False, "shape_lock": "source",
               "instruction": "只保留男人，移除旁边的女人，保留男人脸部身体和背景"}
    payload.update(updates)
    return payload


class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = catalog_skill()

    def test_actual_sorona_ten_colors_and_metadata(self):
        selected = validate_selections(self.skill, intake_payload())
        self.assertEqual(selected[0]["colors"], TEN_COLORS)
        self.assertEqual(selected[0]["garment_name"], "23 索罗娜短袖")
        for ref, detail in zip(self.skill.types["23-sorona-tee"].colors, selected[0]["color_details"]):
            self.assertEqual(detail, {"label": ref.label, "name_zh": ref.name_zh,
                                      "code": ref.code, "hex": ref.hex, "rgb": list(ref.rgb)})
        self.assertEqual(selected[0]["color_details"][-1]["code"], "GN[0]")

    def test_multistyle_order_and_duplicate_merge_matches_service(self):
        rows = [{"garment_type": "07-washed-tee", "colors": ["black"]},
                {"garment_type": "23-sorona-tee", "colors": ["red", "white", "red"]},
                {"garment_type": "23-sorona-tee", "colors": ["forest-green", "red"]}]
        selected = validate_selections(self.skill, {"selections": rows})
        self.assertEqual([row["garment_type"] for row in selected], ["07-washed-tee", "23-sorona-tee"])
        self.assertEqual([row["colors"] for row in selected], [["black"], ["red", "white", "forest-green"]])
        copied = job_selections(selected)
        copied[1]["colors"].clear()
        self.assertEqual(len(selected[1]["colors"]), 3)

    def test_empty_selection_is_not_first_style_or_all_colors(self):
        self.assertEqual(validate_selections(self.skill, intake_payload(selections=[])), [])
        self.assertEqual(validate_selections(self.skill, {}), [])
        with self.assertRaises(ValueError):
            validate_selections(self.skill, {"selections": [{"garment_type": "23-sorona-tee", "colors": []}]})

    def test_legacy_missing_selection_uses_requested_style_and_all_colors(self):
        selected = validate_selections(self.skill, {"garment_type": "23-sorona-tee", "colors": []})
        self.assertEqual(selected[0]["colors"], TEN_COLORS)

    def test_invalid_choices_fail_without_silent_fallback(self):
        for rows in (None, "black", ["bad"], [{}],
                     [{"garment_type": "not-a-real-style", "colors": ["black"]}],
                     [{"garment_type": "23-sorona-tee", "colors": ["not-a-real-color"]}],
                     [{"garment_type": "23-sorona-tee", "colors": "black"}],
                     [{"garment_type": "23-sorona-tee", "colors": [None]}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_selections(self.skill, {"selections": rows})

    def test_unknown_price_and_person_edit_context(self):
        selected = validate_selections(self.skill, intake_payload())
        context = batch_context(selections=selected, source_count=1, output_count=10,
                                settings={"subject_resolved": "person", "pattern_mode": "preserve"},
                                estimate={"priced": False, "unit": None, "total": None})
        text = describe_context(context)
        self.assertIn("单价未知", text)
        self.assertIn("预计总价未知", text)
        intro = agent._intake_intro(agent.AgentContext(selections=selected, batch_context=context,
                                                       color="黑色", subject="person", count=10), "只保留男人")
        self.assertIn("forest-green", intro)
        self.assertIn("#2B5F48", intro)
        self.assertIn("GN[0]", intro)
        self.assertNotIn("颜色：黑色", intro)
        self.assertIn("只保留男人", intro)
        self.assertIn("不是冲突", intro)
        result = harness.analyze_local(mode="intake", instruction="只保留男人", base_prompt=batch_prompt(context),
                                       count=10, batch_context=context)
        self.assertIn("forest-green", result.analysis)
        self.assertIn("仅编辑所指人物", result.analysis)
        self.assertNotIn("人物/背景/光线等仍锁死", result.analysis)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # These guards make accidental live startup or network traffic a hard failure.
        self.guard = patch.object(api, "Workbench", side_effect=AssertionError("no Workbench/credential startup"))
        self.guard.start()
        self.net_guard = patch.object(socket.socket, "connect", side_effect=AssertionError("offline only"))
        self.net_guard.start()
        self.addCleanup(self.guard.stop)
        self.addCleanup(self.net_guard.stop)
        self.app = api.create_app()
        self.endpoint = next(route.endpoint for route in self.app.routes if getattr(route, "path", "") == "/api/harness/stream")

    async def request(self, bench, payload, turns=None):
        self.app.state.wb = bench

        async def request_json():
            return copy.deepcopy(payload)

        request = SimpleNamespace(app=self.app, state=SimpleNamespace(account="offline"), json=request_json)
        chat = ChatScript(turns or [{"content": "mock response, not an actual model message"}])
        with patch.object(agent.httpx, "AsyncClient", side_effect=chat.client), \
             patch.object(agent, "encode_image", return_value="data:image/jpeg;base64,MOCK"), \
             patch.object(api.subject_mod, "detect", return_value="person"), \
             patch.object(api, "inspect_images", return_value="MOCK 原始尺寸 1254×1254；两个人；来源衣服红色"):
            response = await self.endpoint(request)
            if response.status_code != 200:
                return response, [], chat
            chunks = [chunk async for chunk in response.body_iterator]
        frames = []
        for raw in chunks:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            for block in text.split("\n\n"):
                if not block.strip():
                    continue
                event, data = block.split("\n", 1)
                frames.append({"event": event.removeprefix("event: "), "data": json.loads(data.removeprefix("data: "))})
        return response, frames, chat

    async def test_ten_colors_reach_real_agent_mock_stream_and_submission(self):
        bench = FakeBench()
        turns = [tool_turn("inspect_images", {}), tool_turn("estimate_cost", {"count": 9}),
                 tool_turn("set_requirements", {"text": "只保留男人，移除女人，保留男人脸身体与背景"}),
                 tool_turn("submit_generation", {}), {"content": "mock complete"}]
        response, frames, chat = await self.request(bench, intake_payload(), turns)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(bench.created), 1)
        submitted = bench.created[0]
        self.assertEqual(submitted["selections"][0]["colors"], TEN_COLORS)
        self.assertEqual(submitted["output_format"], "webp")
        self.assertEqual(submitted["size"], "1536x1024")
        self.assertFalse(submitted["send_reference"])
        self.assertIn("只保留男人", submitted["extra_requirements"])
        first_user = chat.requests[0]["messages"][-1]["content"][0]["text"]
        for label in TEN_COLORS:
            self.assertIn(label, first_user)
        self.assertIn('"output_count":10', first_user)
        self.assertIn('"source_count":1', first_user)
        self.assertNotIn("颜色：黑色", first_user)
        self.assertEqual(bench.pricer.calls, [("mock-a", "mock-image", 10, "1536x1024")] * 2)
        result = next(frame["data"]["text"] for frame in frames if frame["event"] == "result" and frame["data"]["name"] == "estimate_cost")
        self.assertIn("0.65 CNY", result)
        self.assertNotIn("0.585", result)
        prompt = next(frame["data"] for frame in frames if frame["event"] == "prompt")
        self.assertEqual(len(prompt["target_prompts"]), 10)
        self.assertEqual([row["color"] for row in prompt["target_prompts"]], TEN_COLORS)
        for target, ref in zip(prompt["target_prompts"], bench.skill.types["23-sorona-tee"].colors):
            self.assertIn(ref.hex, target["prompt"])
            self.assertEqual(target["requirements"], submitted["extra_requirements"])
            self.assertIn("保留男人脸身体与背景", target["prompt"])
        self.assertEqual(bench.started, ["mock-created"])

    async def test_multistyle_source_count_not_client_count_or_attachment_limit(self):
        bench = FakeBench(sources=7)
        rows = intake_payload()["selections"] + [{"garment_type": "07-washed-tee", "colors": ["black"]}]
        _, frames, chat = await self.request(bench, intake_payload(selections=rows, count=999))
        meta = next(frame["data"] for frame in frames if frame["event"] == "meta")
        self.assertEqual(meta["count"], 77)
        self.assertEqual(meta["batch_context"]["source_count"], 7)
        self.assertEqual(meta["batch_context"]["total_colors"], 11)
        content = chat.requests[0]["messages"][-1]["content"]
        self.assertEqual(sum(item["type"] == "image_url" for item in content), 6)
        self.assertIn("6/7", content[0]["text"])
        self.assertIn("07-washed-tee", content[0]["text"])

    async def test_provider_specific_unknown_pricing_and_auto_format(self):
        bench = FakeBench()
        _, frames, _ = await self.request(bench, intake_payload(provider="mock-b", output_format="auto"),
                                          [tool_turn("estimate_cost", {}), {"content": "mock"}])
        context = next(frame["data"]["batch_context"] for frame in frames if frame["event"] == "meta")
        self.assertEqual(context["estimate"]["total"], 1.0)
        self.assertEqual(context["settings"]["output_format"], "auto")
        bench = FakeBench()
        _, frames, _ = await self.request(bench, intake_payload(model="unknown-model"),
                                          [tool_turn("estimate_cost", {}), {"content": "mock"}])
        context = next(frame["data"]["batch_context"] for frame in frames if frame["event"] == "meta")
        self.assertIsNone(context["estimate"]["unit"])
        self.assertIsNone(context["estimate"]["total"])
        self.assertTrue(any("单价未知、总价未知" in frame["data"].get("text", "") for frame in frames))

    async def test_local_fallback_keeps_all_colors_settings_and_total(self):
        bench = FakeBench(online=False)
        with patch.object(harness.asyncio, "sleep", return_value=None):
            _, frames, chat = await self.request(bench, intake_payload())
        self.assertFalse(chat.requests)
        prompt = next(frame["data"] for frame in frames if frame["event"] == "prompt")
        self.assertEqual(prompt["batch_context"]["output_count"], 10)
        self.assertEqual(len(prompt["target_prompts"]), 10)
        text = "".join(frame["data"].get("text", "") for frame in frames if frame["event"] == "delta")
        self.assertIn("forest-green", text)
        self.assertIn("webp", text)
        self.assertIn("0.65 CNY", text)
        self.assertFalse(bench.created)

    async def test_empty_selection_can_chat_but_cannot_submit(self):
        bench = FakeBench()
        _, frames, _ = await self.request(bench, intake_payload(selections=[]),
                                          [tool_turn("submit_generation", {}), {"content": "mock"}])
        self.assertFalse(bench.created)
        meta = next(frame["data"] for frame in frames if frame["event"] == "meta")
        self.assertEqual(meta["count"], 0)
        self.assertEqual(meta["batch_context"]["selections"], [])
        self.assertTrue(any("尚未选择款式和颜色" in frame["data"].get("text", "") for frame in frames))

    async def test_invalid_selection_rejected_before_chat_or_create(self):
        bench = FakeBench()
        response, frames, chat = await self.request(bench, intake_payload(selections=[
            {"garment_type": "23-sorona-tee", "colors": ["ultraviolet-invalid"]}]))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(chat.requests)
        self.assertFalse(bench.created)
        self.assertFalse(frames)

    async def test_invalid_tool_args_never_submit_or_mutate_selection(self):
        bench = FakeBench()
        turns = [tool_turn("submit_generation", {"selections": [{"garment_type": "07-washed-tee", "colors": ["black"]}]}),
                 tool_turn("set_requirements", {"text": "bad", "pattern_mode": "invalid"}),
                 tool_turn("submit_generation", ["invalid-json-shape"]),
                 {"content": "mock"}]
        _, frames, _ = await self.request(bench, intake_payload(), turns)
        self.assertFalse(bench.created)
        prompt = next(frame["data"] for frame in frames if frame["event"] == "prompt")
        self.assertEqual(prompt["batch_context"]["selections"][0]["colors"], TEN_COLORS)
        self.assertEqual(prompt["requirements"], "")
        self.assertEqual(sum(frame["event"] == "result" and "失败" in frame["data"]["text"] for frame in frames), 3)

    async def test_agent_selection_mutation_cannot_change_submitted_plan(self):
        bench = FakeBench()

        async def mutated_agent(*, ctx, **kwargs):
            ctx.selections[0]["colors"] = ["black"]
            result = await agent._call_tool(ctx, "submit_generation", {"extra_requirements": "只保留男人"})
            yield {"event": "result", "data": {"name": "submit_generation", "text": result}}
            yield {"event": "prompt", "data": {"requirements": ctx.requirements}}

        with patch.object(agent, "run_agent", side_effect=mutated_agent):
            await self.request(bench, intake_payload())
        self.assertEqual(bench.created[0]["selections"][0]["colors"], TEN_COLORS)

    async def test_rework_uses_exact_tasks_not_intake_selection_or_first_color(self):
        bench = FakeBench()
        tasks = [SimpleNamespace(id="t-red", garment="23-sorona-tee", garment_name="23 索罗娜短袖",
                                 color="red", color_name="红色", color_hex="#E7100E", source="inputs/one.jpg",
                                 item="item-01", view="front", subject="person", prompt="red original task", out="red.webp"),
                 SimpleNamespace(id="t-black", garment="07-washed-tee", garment_name="07 水洗T恤",
                                 color="black", color_name="黑色", color_hex="#212224", source="inputs/two.jpg",
                                 item="item-02", view="back", subject="person", prompt="black original task", out="black.webp")]
        bench.source_job = SimpleNamespace(params={"garment_type": "23-sorona-tee", "provider": "mock-b",
                                                    "model": "mock-image", "size": "1536x1024", "output_format": "webp"},
                                           tasks=tasks)
        payload = {"mode": "rework", "job_id": "20261107-112438-4858", "task_ids": ["t-red", "t-black"],
                   "count": 999, "selections": [{"garment_type": "23-sorona-tee", "colors": ["white"]}],
                   "instruction": "只保留男人", "subject": "person"}
        with patch.object(api, "rework_images", return_value=([], [])), \
             patch.object(api.subject_mod, "task_kind", return_value="person"):
            _, frames, _ = await self.request(bench, payload, [tool_turn("submit_rework", {"instruction": "只保留男人"}),
                                                              {"content": "mock"}])
        context = next(frame["data"]["batch_context"] for frame in frames if frame["event"] == "meta")
        self.assertEqual(context["output_count"], 2)
        self.assertEqual([row["colors"] for row in context["selections"]], [["red"], ["black"]])
        self.assertEqual([row["task_id"] for row in context["tasks"]], ["t-red", "t-black"])
        self.assertEqual(bench.reworked[0][1], ["t-red", "t-black"])
        self.assertEqual(bench.reworked[0][3]["provider"], "mock-b")
        self.assertEqual(bench.reworked[0][3]["output_format"], "webp")
        self.assertFalse(bench.created)
        self.assertEqual([row["code"] for row in context["tasks"]], ["RD[0]", "BK[0]"])

    async def test_empty_or_invalid_rework_does_not_expand_to_all_tasks(self):
        bench = FakeBench()
        bench.source_job = SimpleNamespace(params={"provider": "mock-a", "model": "mock-image"},
                                           tasks=[SimpleNamespace(id="existing", out="existing.webp")])
        payload = {"mode": "rework", "job_id": "20261107-112438-4858", "task_ids": [],
                   "instruction": "只保留男人"}
        with patch.object(api, "rework_images", return_value=([], [])):
            _, frames, _ = await self.request(bench, payload, [tool_turn("submit_rework", {"instruction": "只保留男人"}),
                                                              {"content": "mock"}])
            self.assertFalse(bench.reworked)
            self.assertEqual(next(frame["data"]["count"] for frame in frames if frame["event"] == "meta"), 0)
            payload["task_ids"] = ["missing"]
            response, _, chat = await self.request(bench, payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(chat.requests)

    async def test_all_supplied_generation_settings_survive_submission(self):
        bench = FakeBench()
        options = {"concurrency": 3, "generation_mode": "single_pass", "rpm": 12,
                   "seed": 0, "max_retries": 2, "timeout_sec": 45, "input_fidelity": "high"}
        _, frames, _ = await self.request(bench, intake_payload(**options),
                                          [tool_turn("submit_generation", {}), {"content": "mock"}])
        context = next(frame["data"]["batch_context"] for frame in frames if frame["event"] == "meta")
        for name, value in options.items():
            self.assertEqual(context["settings"][name], value)
            self.assertEqual(bench.created[0]["mode" if name == "generation_mode" else name], value)

    async def test_validated_pattern_change_keeps_complete_color_plan(self):
        bench = FakeBench()
        turns = [tool_turn("set_requirements", {"text": "每个颜色设计不同冬日图案", "pattern_mode": "per_color_variant"}),
                 tool_turn("submit_generation", {}), {"content": "mock"}]
        _, frames, _ = await self.request(bench, intake_payload(), turns)
        self.assertEqual(bench.created[0]["pattern_mode"], "per_color_variant")
        self.assertEqual(bench.created[0]["selections"][0]["colors"], TEN_COLORS)
        prompt = next(frame["data"] for frame in frames if frame["event"] == "prompt")
        self.assertEqual(prompt["batch_context"]["settings"]["pattern_mode"], "per_color_variant")
        self.assertEqual(len(prompt["target_prompts"]), 10)

    async def test_malformed_tool_json_does_not_become_empty_submit(self):
        bench = FakeBench()
        turn = tool_turn("submit_generation", {})
        turn["tool_calls"][0]["function"]["arguments"] = "{"
        _, frames, _ = await self.request(bench, intake_payload(), [turn, {"content": "mock"}])
        self.assertFalse(bench.created)
        self.assertTrue(any("JSON 对象" in frame["data"].get("text", "") for frame in frames))

    async def test_mode_mismatched_tools_are_blocked(self):
        called = []

        async def submit(text):
            called.append(text)
            return "成功"

        ctx = agent.AgentContext(mode="intake", submit_rework=submit)
        result = await agent._call_tool(ctx, "submit_rework", {"instruction": "keep man"})
        self.assertIn("失败", result)
        ctx = agent.AgentContext(mode="rework", submit_generation=submit)
        result = await agent._call_tool(ctx, "submit_generation", {})
        self.assertIn("失败", result)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main(verbosity=2)