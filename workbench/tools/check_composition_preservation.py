"""Offline composition/reference regressions; no paid calls or image-quality claims.

Run: python -B workbench/tools/check_composition_preservation.py
Synthetic front/back canvases, collapsed current results and recording providers only.
All fixtures/job state live in a TemporaryDirectory under PI_SCRATCH_DIR when set.
HTTP and non-loopback connections are blocked (Windows asyncio needs loopback).
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from dataclasses import replace
import ipaddress
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from PIL import Image, ImageDraw

from skillbridge import prompts, refine
from skillbridge.job import Job
from skillbridge.providers.base import EditRequest, Provider
from skillbridge.runner import JobRunner
from skillbridge.skill import ColorRef, GarmentType, Skill


@contextmanager
def no_network():
    def deny(*args, **kwargs):
        raise AssertionError("HTTP/external network is forbidden in this offline check")

    def local_only(method):
        def guarded(sock, address):
            host = address[0] if isinstance(address, tuple) else ""
            try:
                local = host == "localhost" or ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = False
            if not local:
                return deny()
            return method(sock, address)
        return guarded

    with ExitStack() as stack:
        for target in ("httpx.Client.send", "httpx.AsyncClient.send",
                       "urllib.request.urlopen", "socket.create_connection"):
            stack.enter_context(patch(target, side_effect=deny))
        for name in ("connect", "connect_ex"):
            stack.enter_context(patch.object(socket.socket, name, local_only(getattr(socket.socket, name))))
        yield


def canvas(path: Path, views=("front", "back"), color=(65, 72, 86)) -> Path:
    """Draw different per-side artwork, with an intentionally overlapping two-view layout."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (384, 256), (242, 242, 244))
    draw = ImageDraw.Draw(image)
    for index, side in enumerate(views):
        x, y = 30 + 148 * index, 35 - 12 * index
        draw.polygon([(x, y + 32), (x + 42, y + 10), (x + 62, y), (x + 84, y),
                      (x + 104, y + 10), (x + 146, y + 32), (x + 172, y + 100),
                      (x + 132, y + 115), (x + 130, y + 205), (x + 16, y + 205),
                      (x + 14, y + 115), (x - 20, y + 100)], fill=color, outline=(16, 18, 23))
        if side == "front":
            draw.rectangle((x + 54, y + 60, x + 78, y + 82), fill=(220, 40, 70))
            draw.text((x + 30, y + 120), "FRONT 123", fill=(235, 235, 240))
        else:
            draw.ellipse((x + 42, y + 58, x + 100, y + 114), fill=(30, 185, 120))
            draw.text((x + 32, y + 125), "BACK XYZ", fill=(235, 235, 240))
    image.save(path, compress_level=0)
    return path


class RecordingProvider(Provider):
    supports_negative = True

    def __init__(self, capacity: int, result: bytes):
        super().__init__("offline-composition", {})
        self.max_images = capacity
        self.result = result
        self.requests: list[EditRequest] = []
        self.payloads: list[list[bytes]] = []

    async def open(self):
        raise AssertionError("This check must not open a real provider")

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        if not 1 <= len(request.images) <= self.max_images:
            raise AssertionError("Provider image limit exceeded")
        self.requests.append(request)
        self.payloads.append([path.read_bytes() for path in request.images])
        # A deliberately collapsed result: tests prove recovery references, NOT model compliance.
        return self.result


class CompositionChecks(unittest.TestCase):
    def setUp(self):
        self.enterContext(no_network())
        temp = tempfile.TemporaryDirectory(
            prefix="composition-preservation-", dir=os.environ.get("PI_SCRATCH_DIR") or None
        )
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.original = canvas(self.root / "upload-front-back.png")
        self.current = canvas(self.root / "current-front-only.png", ("front",), (35, 75, 155))
        self.effect = canvas(self.root / "effect-single-front.png", ("front",), (180, 35, 95))
        self.swatch = canvas(self.root / "swatch.png", ("front",), (30, 120, 170))
        self.colors = [
            ColorRef("blue", self.swatch, "#1e78aa", (30, 120, 170), "蓝", effect=self.effect),
            ColorRef("pink", self.swatch, "#b4235f", (180, 35, 95), "粉", effect=self.effect),
        ]
        self.garment = GarmentType(
            "fixture-hoodie", ["Fixture hoodie"], self.root, "a fitted long-sleeve hoodie",
            colors=self.colors, cuff="ribbed", sleeve="long", fit="fitted with tapered sleeves",
        )
        self.skill = Skill(
            self.root, {self.garment.slug: self.garment},
            "Transform only the visible garment into [TARGET_GARMENT_TYPE]. "
            + prompts.PRINT_CLAUSE + " " + prompts.SUBJECT_CLAUSE,
            "Do not blur the photograph.", 2_000_000,
        )
        self.serial = 0

    def make_runner(self, *, mode="single_pass", capacity=3, colors=None, **params):
        self.serial += 1
        root = self.root / f"job-{self.serial}"
        source = root / "inputs" / "item-01" / "view-01.png"
        source.parent.mkdir(parents=True)
        source.write_bytes(self.original.read_bytes())
        selected = self.colors if colors is None else colors
        garment = replace(self.garment, colors=selected)
        job = Job("20261106-120000-test", {"mode": mode, "subject": "garment", "concurrency": 1,
                  "max_retries": 1, "retry_on_person": False, **params}, root=root)
        provider = RecordingProvider(capacity, self.current.read_bytes())
        runner = JobRunner(job, self.skill, garment, selected, provider, data_dir=self.root)
        runner.plan()
        self.assertIsNone(runner.gate)
        return runner, provider

    def make_rework(self, *, capacity=3):
        runner, provider = self.make_runner(mode="rework", capacity=capacity, colors=self.colors[:1])
        task = runner.job.tasks[0]
        current = runner.root / task.source
        current.write_bytes(self.current.read_bytes())
        original = canvas(runner.root / "originals" / "item-01" / "view-01.png")
        other = canvas(runner.root / "originals" / "item-02" / "view-02.png", ("back",))
        runner.job.params["original_sources"] = {task.id: [self.rel(runner, original)]}
        task.prompt = refine.compose_template("恢复原图正反面，保留已修改颜色", task.prompt, subject="garment")
        return runner, provider, task, current, original, other

    @staticmethod
    def rel(runner, path):
        return path.relative_to(runner.root).as_posix()

    def assert_composition(self, text, subject="garment"):
        self.assertEqual(text.count(prompts.COMPOSITION_CLAUSE), 1)
        self.assertIn("ALL original garment views and garments", text)
        self.assertIn("front AND back together", text)
        self.assertIn("overlap and relative arrangement", text)
        self.assertIn("If the original upload contains only one view", text)
        self.assertIn("do NOT invent a back view", text)
        self.assertIn("each side's own unique artwork", text)
        if subject == "garment":
            self.assertTrue(text.startswith(prompts.NO_PERSON_CLAUSE))
            self.assertEqual(text.count(prompts.NO_PERSON_CLAUSE), 1)
            self.assertNotIn("ONLY the empty garment", text)
            self.assertNotIn("Keep the exact same person", text)
        self.assertEqual(prompts.enforce_composition_rule(text), text)

    def assert_request(self, request):
        self.assert_composition(request.prompt)
        self.assertTrue(request.negative.startswith(prompts.NEGATIVE_NO_PERSON))
        self.assertIn(prompts.NEGATIVE_COMPOSITION, request.negative)

    def test_all_builders_and_negatives_keep_conditional_canvas_rule(self):
        for kind in ("garment", "person", ""):
            for effect in (False, True):
                for mode in prompts.PATTERN_MODE_KEYS:
                    with self.subTest(subject=kind, effect=effect, mode=mode):
                        texts = [
                            prompts.build_single_pass(self.skill, self.garment, self.colors[0],
                                                      pattern_mode=mode, subject=kind, effect=effect),
                            prompts.build_style_only(self.garment, subject=kind, effect=effect),
                            prompts.build_recolor_only(self.colors[0], garment=self.garment,
                                                      pattern_mode=mode, subject=kind, effect=effect),
                            prompts.build_master_recolor(self.colors[0], garment=self.garment,
                                                        subject=kind, effect=effect),
                        ]
                        for text in texts:
                            self.assert_composition(text, kind)
                        negative = prompts.full_negative(self.skill, mode, self.garment, kind, effect)
                        self.assertIn(prompts.NEGATIVE_COMPOSITION, negative)
                        self.assertIn("Unless artwork changes were explicitly requested", negative)
                        if kind == "garment":
                            self.assertTrue(negative.startswith(prompts.NEGATIVE_NO_PERSON))
                        if mode != "preserve":
                            self.assertNotIn(prompts.PRINT_LOCK_CLAUSE, texts[0])
                            self.assertNotIn(prompts.PRINT_LOCK_CLAUSE, texts[2])
                        if effect:
                            self.assertIn("IMAGE 2 controls ONLY garment cut", texts[0])
                            self.assertIn("NEVER the ORIGINAL UPLOAD canvas composition", texts[0])
                            self.assertIn("IMAGE 2 is color-only, NEVER an authority", texts[3])

    def test_composition_enforcement_is_idempotent_including_saved_v91(self):
        for base in ("Edit this photo.", prompts.NO_PERSON_CLAUSE, prompts._LEGACY_NO_PERSON_CLAUSE):
            text = "Before " + prompts.COMPOSITION_CLAUSE + " After " + base + " " + prompts.COMPOSITION_CLAUSE
            revised = prompts.enforce_composition_rule(text)
            self.assertEqual(prompts.enforce_composition_rule(revised), revised)
            self.assertEqual(revised.count(prompts.COMPOSITION_CLAUSE), 1)
            if base != "Edit this photo.":
                self.assertTrue(revised.startswith(prompts.NO_PERSON_CLAUSE))
                self.assertNotIn("ONLY the empty garment", revised)

    def test_rework_strips_known_old_and_current_effect_master_fit_roles(self):
        fragments = [prompts._LEGACY_EFFECT_CLAUSE, prompts.EFFECT_CLAUSE,
                     prompts._LEGACY_MASTER_EFFECT_CLAUSE, prompts.MASTER_EFFECT_CLAUSE,
                     prompts.SILHOUETTE_LOCK_CLAUSE]
        for fragment in fragments:
            for kind in ("garment", "person", ""):
                base = fragment + prompts.FIT_TEMPLATE.format(fit="fitted sleeves")
                base += prompts.color_hint(self.colors[0]) + " " + prompts.MATERIAL_CLAUSE
                base += " " + prompts.original_upload_reference_clause(3)
                base = prompts.enforce_subject_rule(base, kind)
                revised = prompts.build_rework_reference_prompt(base, kind, original_count=1)
                self.assert_composition(revised, kind)
                for forbidden in ("official product photo", "RESHAPE, DO NOT RECOLOUR",
                                  "also shows this style's fit", "attached colour reference image",
                                  "ORIGINAL UPLOAD REFERENCE", "IMAGE 3", "COLOUR PASS ON THE APPROVED MASTER"):
                    self.assertNotIn(forbidden, revised)
                self.assertIn("IMAGE 1 is the current edit target", revised)
                self.assertIn("IMAGE 2 is the original upload, NOT style/effect references", revised)
                self.assertIn("do NOT copy original fabric color back", revised)
                self.assertIn(prompts.MATERIAL_CLAUSE, revised)
                self.assertEqual(prompts.build_rework_reference_prompt(revised, kind, 1), revised)

    def test_rework_roles_rebind_to_actual_attachment_count(self):
        text = "Change only requested garment details."
        for count in (2, 0, 1, 3, 0):
            text = prompts.build_rework_reference_prompt(text, "garment", count)
            self.assert_composition(text)
            self.assertEqual(text.count("REWORK REFERENCE ROLES —"), 1)
            if count == 0:
                self.assertIn("no original upload is attached", text)
                self.assertNotIn("IMAGE 2 is the original upload", text)
                self.assertNotIn("IMAGE 3", text)
            else:
                self.assertNotIn("no original upload is attached", text)
                self.assertIn(prompts.rework_reference_clause(count, "garment"), text)
                if count > 1:
                    self.assertIn("IMAGE 2 is the original canvas corresponding to this edit task", text)
                    self.assertIn("Do not combine independently uploaded front/back files", text)
            self.assertEqual(prompts.build_rework_reference_prompt(text, "garment", count), text)

    def test_operator_artwork_changes_are_not_revoked_by_new_locks(self):
        instruction = "每个颜色用不同图案"
        base = prompts.apply_requirement(prompts.PRINT_LOCK_CLAUSE, instruction, subject="garment")
        self.assertNotIn(prompts.PRINT_LOCK_CLAUSE, base)
        revised = refine.compose_template(instruction, base, pattern_mode="per_color_variant", subject="garment")
        revised = prompts.build_rework_reference_prompt(revised, "garment", 1)
        self.assert_composition(revised)
        self.assertIn("PATTERN VARIETY", revised)
        self.assertIn("pattern variants still win", revised)
        self.assertIn("do not overwrite intentionally revised artwork", revised)
        self.assertNotIn(prompts.PRINT_LOCK_CLAUSE, revised)
        for requirement in ("", instruction):
            self.assert_composition(prompts.apply_requirement("Edit.", requirement, subject="garment"))

    def test_refine_template_and_mocked_llm_are_composition_safe(self):
        self.assert_composition(refine.compose_template("背景提亮", prompts.EFFECT_CLAUSE, subject="garment"))
        with patch("skillbridge.refine.compose_llm", new=AsyncMock(return_value="Adjust lighting. " + prompts.EFFECT_CLAUSE)):
            text, used = asyncio.run(refine.compose(
                "背景提亮", "Old brief", refiner="llm", subject="garment",
                cfg={"enabled": True, "model": "offline", "api_key_env": "FIXTURE_KEY"},
                env={"FIXTURE_KEY": "not-a-real-key"},
            ))
        self.assertEqual(used, "llm")
        self.assert_composition(text)
        self.assertNotIn("official product photo", text)
        self.assertIn("no original upload is attached", text)

    def test_single_generation_preserves_upload_effect_bytes_and_order(self):
        runner, provider = self.make_runner(colors=self.colors[:1])
        task = runner.job.tasks[0]
        source = runner.root / task.source
        before = {p: p.read_bytes() for p in (source, self.effect, self.swatch)}
        asyncio.run(runner._execute_color_task(task))
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].images, [source, self.effect])
        self.assertEqual(provider.payloads[0], [before[source], before[self.effect]])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assert_request(provider.requests[0])
        self.assertNotIn("IMAGE 3", provider.requests[0].prompt)
        self.assertEqual(task.attempts, 1)

    def test_master_derivation_adds_original_as_image3_only_when_supported(self):
        for capacity in (3, 2, 1):
            runner, provider = self.make_runner(capacity=capacity)
            master, derived = runner.job.tasks
            source = runner.root / master.source
            before = source.read_bytes()
            asyncio.run(runner._run_single_pass())
            self.assertEqual(len(provider.requests), 2)
            request = provider.requests[1]
            expected = [runner.root / master.out]
            if capacity > 1:
                expected.append(self.effect)
            if capacity > 2:
                expected.append(source.resolve())
                self.assertIn("IMAGE 3 is the customer's ORIGINAL UPLOAD", request.prompt)
                self.assertEqual(provider.payloads[1][2], before)
            else:
                self.assertNotIn("IMAGE 3", request.prompt)
                self.assertNotIn("ORIGINAL UPLOAD REFERENCE", request.prompt)
            self.assertEqual(request.images, expected)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(derived.attempts, 1)
            for captured in provider.requests:
                self.assert_request(captured)

    def test_color_swatch_remains_image2_before_original_image3(self):
        colors = [replace(color, effect=None) for color in self.colors]
        runner, provider = self.make_runner(colors=colors)
        master, derived = runner.job.tasks
        source = runner.root / master.source
        asyncio.run(runner._run_single_pass())
        self.assertEqual(provider.requests[1].images,
                         [runner.root / master.out, self.swatch, source.resolve()])
        self.assertEqual(provider.payloads[1][1:], [self.swatch.read_bytes(), source.read_bytes()])
        self.assertIn("IMAGE 3 is the customer's ORIGINAL UPLOAD", provider.requests[1].prompt)

    def test_source_override_equal_to_original_does_not_duplicate_it(self):
        runner, provider = self.make_runner(colors=self.colors[:1])
        task = runner.job.tasks[0]
        source = runner.root / task.source
        asyncio.run(runner._execute_color_task(task, source_override=source))
        self.assertEqual(provider.requests[0].images, [source, self.effect])
        self.assertNotIn("ORIGINAL UPLOAD REFERENCE", provider.requests[0].prompt)
        self.assertEqual(len(provider.requests), 1)

    def test_original_can_be_image2_when_no_effect_or_color_reference_is_attached(self):
        runner, provider = self.make_runner(capacity=2, send_reference=False)
        master, derived = runner.job.tasks
        asyncio.run(runner._run_single_pass())
        request = provider.requests[1]
        self.assertEqual(request.images, [runner.root / master.out, (runner.root / derived.source).resolve()])
        self.assertIn("IMAGE 2 is the customer's ORIGINAL UPLOAD", request.prompt)
        self.assertNotIn("IMAGE 3", request.prompt)
        self.assertNotIn("official product photo", request.prompt)

    def test_two_phase_attaches_original_without_extra_calls_or_false_image3_roles(self):
        for capacity in (3, 2, 1):
            runner, provider = self.make_runner(mode="two_phase", capacity=capacity)
            original = runner.root / runner.job.tasks[0].source
            before = original.read_bytes()
            asyncio.run(runner._run_two_phase())
            self.assertEqual(len(provider.requests), 3)  # one planned style pass, two color passes
            self.assertEqual(provider.requests[0].images, [original])
            for request, payload in zip(provider.requests[1:], provider.payloads[1:]):
                self.assertEqual(len(request.images), capacity)
                if capacity > 1:
                    self.assertEqual(request.images[1], self.effect)
                if capacity > 2:
                    self.assertEqual(request.images[2], original.resolve())
                    self.assertEqual(payload[2], before)
                    self.assertIn("IMAGE 3 is the customer's ORIGINAL UPLOAD", request.prompt)
                else:
                    self.assertNotIn("IMAGE 3", request.prompt)
            for request in provider.requests:
                self.assert_request(request)
            self.assertEqual(original.read_bytes(), before)

    def test_rework_current_plus_distinct_originals_respects_limit_and_color_authority(self):
        for capacity in (4, 2, 1):
            runner, provider, task, current, original, other = self.make_rework(capacity=capacity)
            runner.job.params["original_sources"][task.id] = [
                self.rel(runner, original), self.rel(runner, original).replace("/", "\\"),
                self.rel(runner, other), self.rel(runner, original),
            ]
            before = {p: p.read_bytes() for p in (current, original, other)}
            asyncio.run(runner._execute_rework_task(task))
            self.assertEqual(len(provider.requests), 1)
            request = provider.requests[0]
            expected = [current.resolve(), *[original.resolve(), other.resolve()][:capacity - 1]]
            self.assertEqual(request.images, expected)
            self.assertEqual(provider.payloads[0], [before[p] for p in expected])
            self.assertEqual({p: p.read_bytes() for p in before}, before)
            self.assert_request(request)
            self.assertIn(prompts.rework_reference_clause(len(expected) - 1, task.subject), request.prompt)
            self.assertNotIn("official product photo", request.prompt)
            self.assertNotIn(self.effect, request.images)
            self.assertEqual(task.attempts, 1)
            if capacity == 1:
                self.assertTrue(any("原始上传未附加" in line["msg"] for line in runner.job.log))
                self.assertIn("no original upload is attached", request.prompt)
            elif capacity == 2:
                self.assertNotIn("IMAGE 3", request.prompt)
                self.assertTrue(any("1/2 张原始上传" in line["msg"] for line in runner.job.log))

    def test_rework_map_is_per_selected_task_not_a_pool_of_other_item_views(self):
        runner, provider, task, current, original, other = self.make_rework(capacity=4)
        second = replace(task, id="other-item-view", item="item-02", view="view-02", out=None)
        runner.job.tasks.append(second)
        runner.job.params["original_sources"][second.id] = [self.rel(runner, other)]
        asyncio.run(runner._execute_rework_task(task))
        asyncio.run(runner._execute_rework_task(second))
        self.assertEqual(provider.requests[0].images, [current.resolve(), original.resolve()])
        self.assertEqual(provider.requests[1].images, [current.resolve(), other.resolve()])
        self.assertEqual(len(provider.requests), 2)

    def test_missing_malformed_and_unsafe_origins_never_attach_arbitrary_paths(self):
        for mapping_kind in ("invalid", "missing", "string", "other-task", "none"):
            runner, provider, task, current, original, other = self.make_rework()
            if mapping_kind == "invalid":
                entries = [str(self.original), "../upload-front-back.png", "originals/../../upload-front-back.png",
                           "C:/arbitrary.png", "C:arbitrary.png", "//server/share/upload.png",
                           self.rel(runner, current), "originals/missing.png", "originals",
                           "originals/file.png:stream", "originals/\x00.png", None, {}]
                (runner.root / "originals" / "not-an-image.txt").write_text("not an image", encoding="utf-8")
                entries.append("originals/not-an-image.txt")
                runner.job.params["original_sources"] = {task.id: entries}
            elif mapping_kind == "missing":
                original.unlink()
            elif mapping_kind == "string":
                runner.job.params["original_sources"] = {task.id: self.rel(runner, original)}
            elif mapping_kind == "other-task":
                runner.job.params["original_sources"] = {"not-selected": [self.rel(runner, original)]}
            else:
                runner.job.params.pop("original_sources")
            asyncio.run(runner._execute_rework_task(task))
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(provider.requests[0].images, [current.resolve()])
            self.assertIn("no original upload is attached", provider.requests[0].prompt)
            self.assertNotIn("IMAGE 3", provider.requests[0].prompt)
            self.assertTrue(any("未找到可用原始上传" in line["msg"] for line in runner.job.log))

    def test_symlink_original_cannot_escape_job_root(self):
        runner, provider, task, current, original, other = self.make_rework()
        link = runner.root / "originals" / "escaping.png"
        try:
            link.symlink_to(self.original)
        except OSError as exc:
            self.skipTest(f"OS does not allow creating test symlinks: {exc}")
        runner.job.params["original_sources"][task.id] = [self.rel(runner, link), self.rel(runner, original)]
        asyncio.run(runner._execute_rework_task(task))
        self.assertEqual(provider.requests[0].images, [current.resolve(), original.resolve()])

    def test_unsafe_current_target_fails_without_any_provider_call(self):
        runner, provider, task, *_ = self.make_rework()
        task.source = str(self.current)
        asyncio.run(runner._execute_rework_task(task))
        self.assertEqual(task.status, "failed")
        self.assertEqual(provider.requests, [])
        self.assertIn("路径不安全", task.error)

    def test_saved_generation_prompt_is_guarded_at_runner_boundary(self):
        runner, provider = self.make_runner(colors=self.colors[:1])
        task = runner.job.tasks[0]
        task.prompt = "Change fabric color only. " + prompts._LEGACY_NO_PERSON_CLAUSE
        asyncio.run(runner._execute_color_task(task))
        self.assert_request(provider.requests[0])

    def test_http_guard_is_active(self):
        with httpx.Client() as client, self.assertRaises(AssertionError):
            client.get("https://composition-check.invalid/")
        with socket.socket() as sock, self.assertRaises(AssertionError):
            sock.connect(("192.0.2.1", 443))


if __name__ == "__main__":
    unittest.main(verbosity=2)
