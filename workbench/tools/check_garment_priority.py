"""Offline garment-output priority regressions; no vision or paid-call claims.

Run: python -B workbench/tools/check_garment_priority.py
Only synthetic fixtures and a recording provider are used. All test data lives in
an isolated TemporaryDirectory, never in the existing data/test directories.
External connections and HTTP sends are blocked for the entire test run.
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
from unittest.mock import patch
import urllib.request

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from PIL import Image, ImageDraw

from skillbridge import prompts, refine, subject
from skillbridge.job import Job
from skillbridge.providers.base import EditRequest, Provider
from skillbridge.runner import JobRunner
from skillbridge.skill import ColorRef, GarmentType, Skill, load_skill


@contextmanager
def no_external_network():
    """Block HTTP entirely; allow loopback sockets needed by Windows asyncio."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def deny(*args, **kwargs):
        raise AssertionError("External network/HTTP is forbidden in this offline check")

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
        for target in ("httpx.Client.send", "httpx.AsyncClient.send", "urllib.request.urlopen",
                       "socket.create_connection"):
            stack.enter_context(patch(target, side_effect=deny))
        stack.enter_context(patch.object(socket.socket, "connect", local_only(original_connect)))
        stack.enter_context(patch.object(socket.socket, "connect_ex", local_only(original_connect_ex)))
        yield


def fixture(path: Path, carrier: str = "") -> Path:
    """Colored shirt plus optional symbolic skin/mannequin; not a detector fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (256, 256), (238, 238, 236))
    draw = ImageDraw.Draw(image)
    draw.polygon([(70, 65), (100, 50), (156, 50), (186, 65), (215, 115),
                  (178, 134), (174, 225), (82, 225), (78, 134), (41, 115)], fill=(30, 55, 125))
    draw.rectangle((110, 105, 145, 145), fill=(20, 20, 24))
    if carrier:
        color = (222, 168, 138) if carrier == "person" else (160, 160, 160)
        draw.ellipse((105, 5, 150, 58), fill=color)
        draw.rectangle((116, 48, 140, 72), fill=color)
        draw.rectangle((22, 80, 52, 185), fill=color)
        draw.rectangle((205, 80, 235, 185), fill=color)
        draw.rectangle((85, 225, 111, 255), fill=color)
        draw.rectangle((145, 225, 171, 255), fill=color)
    image.save(path, compress_level=0)
    return path


class RecordingProvider(Provider):
    """Local request capture only; returning the source does NOT prove no-person output."""
    max_images = 2
    supports_negative = True

    def __init__(self):
        super().__init__("offline-recording", {})
        self.requests: list[EditRequest] = []
        self.result: bytes | None = None

    async def open(self):
        raise AssertionError("Offline tests must not open a provider client")

    async def edit(self, request: EditRequest, timeout: float) -> bytes:
        self.requests.append(request)
        return self.result if self.result is not None else request.images[0].read_bytes()


class GarmentPriorityChecks(unittest.TestCase):
    def setUp(self):
        network = no_external_network()
        network.__enter__()
        self.addCleanup(network.__exit__, None, None, None)
        self.temp = tempfile.TemporaryDirectory(
            prefix="garment-priority-", dir=os.environ.get("PI_SCRATCH_DIR") or None
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plain = fixture(self.root / "plain.png")
        self.worn = fixture(self.root / "worn.png", "person")
        self.mannequin = fixture(self.root / "mannequin.png", "mannequin")
        self.colors = [
            ColorRef("blue", self.worn, "#1e377d", (30, 55, 125), "蓝", effect=self.worn),
            ColorRef("gray", self.mannequin, "#555555", (85, 85, 85), "灰", effect=self.mannequin),
        ]
        self.garment = GarmentType(
            "fixture-tee", ["Fixture tee"], self.root, "a long-sleeve fitted cotton T-shirt",
            colors=self.colors, sleeve="long", cuff="plain", fit="slim with tapered sleeves",
        )
        # Exercise the real loaded skill wording, not a weakened substitute template.
        skill_root = Path(__file__).resolve().parents[2] / "apparel-style-recolor"
        loaded = load_skill(skill_root)
        self.skill = Skill(self.root, {self.garment.slug: self.garment},
                           loaded.prompt_template, loaded.negative_prompt, 2_000_000)

    def assert_no_person_lock_conflicts(self, text: str):
        lower = text.lower()
        for fragment in ("keep the exact same person", "the same person", "that same person",
                         "source image alone decides", "person's pose and body shape",
                         "non-garment pixels", "keep image 1's scene unchanged",
                         "master image pixel-identical", "nothing about it may move",
                         "source photo shows the garment on its own",
                         "change nothing but the garment", "do not change pose",
                         "do not change body shape", "where the source shows only the garment"):
            self.assertNotIn(fragment, lower)

    def assert_garment_prompt(self, text: str):
        self.assertTrue(text.startswith(prompts.NO_PERSON_CLAUSE))
        self.assertEqual(text.count(prompts.NO_PERSON_CLAUSE), 1)
        self.assert_no_person_lock_conflicts(text)
        for fragment in ("REMOVE the original wearer", "IMAGE 2 humans and mannequins are forbidden",
                         "Transfer ONLY the permitted garment cut, color and construction",
                         "source IMAGE 1, style/effect IMAGE 2, approved master and color references",
                         "never a neck or a chest", "Reconstruct garment areas occluded"):
            self.assertIn(fragment, text)

    def assert_garment_request(self, request: EditRequest):
        self.assert_garment_prompt(request.prompt)
        self.assertTrue((request.negative or "").startswith(prompts.NEGATIVE_NO_PERSON))
        self.assert_no_person_lock_conflicts(request.negative or "")

    def make_runner(self, *, mode="single_pass", output_subject="garment", source=None,
                    colors=None, provider_max_images=2, **params):
        root = self.root / f"job-{len(list(self.root.glob('job-*')))}"
        original = root / "inputs" / "item-01" / "item-01.png"
        original.parent.mkdir(parents=True)
        original.write_bytes((source or self.worn).read_bytes())
        job = Job("fixture-202611061200", {"mode": mode, "subject": output_subject,
                  "concurrency": 1, "max_retries": 1, **params}, root=root)
        provider = RecordingProvider()
        provider.max_images = provider_max_images
        runner = JobRunner(job, self.skill, self.garment, colors or self.colors,
                           provider, data_dir=self.root)
        runner.plan()
        self.assertIsNone(runner.gate)
        self.assertFalse(runner.retry_on_person)
        return runner, provider

    def test_subject_override_is_output_rule_not_source_assumption(self):
        self.assertEqual(subject.detect(self.worn), "person")
        self.assertEqual(subject.detect(self.plain), "auto")  # no skin is not evidence of no wearer
        self.assertEqual(subject.detect(self.worn, "garment"), "garment")
        self.assertEqual(subject.detect(self.plain, "person"), "person")
        with patch("skillbridge.subject.measure", side_effect=AssertionError("override must win")):
            self.assertEqual(subject.detect(self.worn, "garment"), "garment")

    def test_all_prompt_paths_have_first_priority_without_conflicts(self):
        for effect in (False, True):
            for mode in prompts.PATTERN_MODE_KEYS:
                with self.subTest(effect=effect, pattern=mode):
                    texts = [
                        prompts.build_single_pass(self.skill, self.garment, self.colors[0],
                                                  subject="garment", effect=effect, pattern_mode=mode),
                        prompts.build_style_only(self.garment, subject="garment", effect=effect),
                        prompts.build_recolor_only(self.colors[0], garment=self.garment,
                                                  subject="garment", effect=effect, pattern_mode=mode),
                        prompts.build_master_recolor(self.colors[0], garment=self.garment,
                                                    subject="garment", effect=effect),
                    ]
                    for text in texts:
                        self.assert_garment_prompt(text)
                        self.assertIn(prompts.MATERIAL_CLAUSE, text)
                        self.assertIn("CUFF & HEM CONSTRUCTION", text)
                    negative = prompts.full_negative(self.skill, mode, self.garment, "garment", effect)
                    self.assertTrue(negative.startswith(prompts.NEGATIVE_NO_PERSON))
                    self.assert_no_person_lock_conflicts(negative)
                    self.assertIn(prompts.cuff_negative(self.garment), negative)
                    self.assertIn(prompts.sleeve_negative(self.garment), negative)
                    if mode == "preserve":
                        self.assertIn(prompts.PRINT_LOCK_CLAUSE, texts[0])
                    else:
                        self.assertNotIn(prompts.PRINT_LOCK_CLAUSE, texts[0])
        locked = prompts.build_single_pass(self.skill, self.garment, self.colors[0], subject="garment")
        self.assertIn(prompts.PRINT_LOCK_CLAUSE, locked)
        self.assertIn("RESHAPE, DO NOT RECOLOUR", locked)
        self.assertIn("adopt IMAGE 2's fit exactly", locked)
        self.assertNotIn(prompts.KEEP_ITEMS["garment"], locked)
        self.assertIn("slim with tapered sleeves", locked)
        master = prompts.build_master_recolor(self.colors[1], garment=self.garment, subject="garment")
        self.assertIn("colour only", master)
        self.assertIn("keep the master's silhouette", master)
        ribbed = replace(self.garment, cuff="ribbed")
        self.assertIn(prompts.CUFF_RIBBED_CLAUSE, prompts.build_style_only(ribbed, subject="garment"))
        for supported in (False, True):
            merged = prompts.with_negative(locked, negative, supported=supported)
            self.assert_garment_prompt(merged)

    def test_rework_requirement_and_legacy_context_are_sanitized(self):
        old = prompts.PRESERVE_CLAUSE + " Do not change pose, body shape or non-garment pixels."
        old += " " + prompts.SUBJECT_CLAUSE + " " + refine.SWITCH_MODEL_CLAUSE
        old += (" NO PERSON — the source photo shows the garment on its own, so keep its "
                "flat-lay or ghost-mannequin presentation and image quality exactly as they are.")
        for requirement in ("", "把图案换成新的图案", "保持图案，背景提亮"):
            revised = prompts.apply_requirement(old, requirement, subject="garment")
            self.assert_garment_prompt(revised)
            self.assertEqual(prompts.enforce_subject_rule(revised, "garment"), revised)
        refined = refine.compose_template("背景提亮", old, subject="garment")
        self.assert_garment_prompt(prompts.enforce_subject_rule(refined, "garment"))

    def test_person_and_auto_remain_non_destructive(self):
        for kind in ("person", "auto", ""):
            texts = [prompts.build_single_pass(self.skill, self.garment, self.colors[0], subject=kind),
                     prompts.build_style_only(self.garment, subject=kind),
                     prompts.build_recolor_only(self.colors[0], garment=self.garment, subject=kind),
                     prompts.build_master_recolor(self.colors[0], garment=self.garment, subject=kind)]
            for text in texts:
                self.assertNotIn(prompts.NO_PERSON_CLAUSE, text)
                self.assertEqual(prompts.enforce_subject_rule(text, kind), text)
                self.assertIn(prompts.PRESERVE_CLAUSE if kind == "person" else prompts.SUBJECT_CLAUSE, text)
                if kind != "person":
                    self.assertNotIn(prompts.PRESERVE_CLAUSE, text)
            self.assertNotIn(prompts.NEGATIVE_NO_PERSON,
                             prompts.full_negative(self.skill, "preserve", self.garment, kind))
        runner, _ = self.make_runner(output_subject="auto", source=self.worn)
        self.assertTrue(all(t.subject == "person" for t in runner.job.tasks))
        runner, _ = self.make_runner(output_subject="auto", source=self.plain)
        self.assertTrue(all(t.subject == "auto" for t in runner.job.tasks))

    def test_single_and_master_requests_keep_both_unmodified_references(self):
        runner, provider = self.make_runner()
        master, derived = runner.job.tasks
        self.assertTrue(master.master)
        self.assertEqual(derived.master_task, master.id)
        source = runner.root / master.source
        original_refs = {p: p.read_bytes() for p in (self.worn, self.mannequin)}
        asyncio.run(runner._execute_color_task(master))
        asyncio.run(runner._execute_shape_task(derived))
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(provider.requests[0].images, [source, self.worn])
        # 派生色改成「基准成品 + 面料色卡(+原图)」：色卡不带版型，避免把效果图的款式抄过来
        derived_images = [Path(x) for x in provider.requests[1].images]
        self.assertEqual(derived_images[0], runner.root / master.out)
        self.assertIn("swatches", str(derived_images[1].parent))
        self.assertEqual(derived_images[-1], runner.root / master.source if len(derived_images) > 2 else derived_images[-1])
        for request in provider.requests:
            self.assert_garment_request(request)
        self.assertTrue(all(t.status == "done" and t.attempts == 1 for t in runner.job.tasks))
        self.assertEqual(original_refs, {p: p.read_bytes() for p in original_refs})

    def test_recolor_color_reference_and_reference_disabled(self):
        colors = [replace(self.colors[0], effect=None)]
        runner, provider = self.make_runner(mode="recolor_only", colors=colors)
        asyncio.run(runner._execute_color_task(runner.job.tasks[0]))
        self.assertEqual(provider.requests[0].images[1], self.worn)
        self.assert_garment_request(provider.requests[0])
        for options in ({"send_reference": False}, {"provider_max_images": 1}):
            runner, provider = self.make_runner(colors=[self.colors[0]], **options)
            asyncio.run(runner._execute_color_task(runner.job.tasks[0]))
            request = provider.requests[0]
            self.assertEqual(len(request.images), 1)
            self.assertNotIn("REFERENCE ROLES — two images are attached", request.prompt)
            self.assert_garment_request(request)

    def test_two_phase_style_and_recolor_requests(self):
        runner, provider = self.make_runner(mode="two_phase")
        asyncio.run(runner._run_two_phase())
        self.assertEqual(len(provider.requests), 3)  # planned style pass + two color passes only
        self.assertEqual(len(provider.requests[0].images), 1)
        for request in provider.requests:
            self.assert_garment_request(request)
        self.assertTrue(all(t.status == "done" for t in runner.job.tasks))

    def test_rework_runner_explicit_override_beats_saved_person_subject(self):
        runner, provider = self.make_runner(mode="rework", colors=[self.colors[0]])
        task = runner.job.tasks[0]
        task.subject = "person"
        task.prompt = refine.compose_template("背景提亮", prompts.PRESERVE_CLAUSE, subject="person")
        task.prompt += " " + refine.SWITCH_MODEL_CLAUSE
        asyncio.run(runner._execute_rework_task(task))
        self.assertEqual(task.subject, "garment")
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(provider.requests[0].images), 1)
        self.assert_garment_request(provider.requests[0])
        self.assertTrue(runner.negative_for(self.garment, "person").startswith(prompts.NEGATIVE_NO_PERSON))

    def test_single_runner_explicit_override_beats_saved_person_subject(self):
        runner, provider = self.make_runner(colors=[self.colors[0]])
        task = runner.job.tasks[0]
        task.subject = "person"
        task.prompt = self.skill.prompt_template + " " + prompts.PRESERVE_CLAUSE
        asyncio.run(runner._execute_color_task(task))
        self.assertEqual(task.subject, "garment")
        self.assertEqual(len(provider.requests), 1)
        self.assert_garment_request(provider.requests[0])

    def test_person_runner_keeps_original_person_and_no_garment_ban(self):
        runner, provider = self.make_runner(output_subject="person", colors=[self.colors[0]])
        asyncio.run(runner._execute_color_task(runner.job.tasks[0]))
        request = provider.requests[0]
        self.assertIn(prompts.PRESERVE_CLAUSE, request.prompt)
        self.assertNotIn(prompts.NO_PERSON_CLAUSE, request.prompt)
        self.assertNotIn(prompts.NEGATIVE_NO_PERSON, request.negative)
        self.assertEqual(request.images[1], self.worn)

    def test_existing_person_alert_is_warning_not_strong_vision_or_extra_call(self):
        # Existing heuristic intentionally skips worn sources; document, do not pretend to fix it.
        self.assertEqual(subject.person_alert(self.worn, self.worn)["reason"], "source-has-person")
        runner, provider = self.make_runner(source=self.plain, colors=[self.colors[0]], max_retries=3)
        provider.result = self.worn.read_bytes()
        asyncio.run(runner._execute_color_task(runner.job.tasks[0]))
        task = runner.job.tasks[0]
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(task.attempts, 1)
        self.assertTrue(task.person_alert)
        self.assertEqual(task.status, "done")

    def test_network_guard_is_active(self):
        with httpx.Client() as client, self.assertRaises(AssertionError):
            client.get("https://offline-test.invalid/")
        with self.assertRaises(AssertionError):
            urllib.request.urlopen("https://offline-test.invalid/")
        with self.assertRaises(AssertionError):
            socket.create_connection(("offline-test.invalid", 443))

        async def async_http():
            async with httpx.AsyncClient() as client:
                await client.get("https://offline-test.invalid/")

        with self.assertRaises(AssertionError):
            asyncio.run(async_http())


if __name__ == "__main__":
    with no_external_network():
        unittest.main(verbosity=2)