"""Offline subject consistency checks: recording providers and synthetic uploads only.

Run: python -B workbench/tools/check_person_consistency.py
All job state lives in a scratch TemporaryDirectory. HTTP/external sockets are
blocked; no service startup, real credentials, Feishu, generation or existing data.
Captured prompts/bytes prove request contracts, not image-model compliance.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from PIL import Image, ImageDraw

from check_garment_priority import fixture, no_external_network
from skillbridge import prompts, refine, subject
from skillbridge.job import Job, Task
from skillbridge.providers.base import EditRequest, Provider
from skillbridge.runner import JobRunner
from skillbridge.service import Workbench
from skillbridge.skill import ColorRef, GarmentType, Skill, load_skill


class RecordingProvider(Provider):
    supports_negative = True

    def __init__(self, capacity, result):
        super().__init__("offline-person", {"model": "fixture-image"})
        self.max_images = capacity
        self.result = result
        self.requests = []
        self.payloads = []

    async def open(self):
        raise AssertionError("No provider clients may open in this test")

    async def edit(self, request, timeout):
        if not 1 <= len(request.images) <= self.max_images:
            raise AssertionError("Image capacity exceeded")
        self.requests.append(request)
        self.payloads.append([path.read_bytes() for path in request.images])
        return self.result  # deliberately empty: request tests do not claim visual recovery


class PersonConsistencyChecks(unittest.TestCase):
    def setUp(self):
        self.enterContext(no_external_network())
        temp = tempfile.TemporaryDirectory(prefix="person-consistency-", dir=os.environ.get("PI_SCRATCH_DIR"))
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.worn = fixture(self.root / "worn-original.png", "person")
        self.empty = fixture(self.root / "damaged-empty.png")
        self.mannequin = fixture(self.root / "effect-mannequin.png", "mannequin")
        # Distinct empty/model/mannequin reference payloads must never decide wearer count.
        self.colors = [ColorRef(name, path, hex_value, rgb, name, effect=path)
                       for name, path, hex_value, rgb in (
                           ("white", self.empty, "#eeeeee", (238, 238, 238)),
                           ("black", self.mannequin, "#222222", (34, 34, 34)),
                           ("gray", self.worn, "#777777", (119, 119, 119)),
                           ("burgundy", self.empty, "#641c35", (100, 28, 53)),
                       )]
        self.garment = GarmentType("fixture-tee", ["Fixture tee"], self.root,
                                   "a long-sleeve fitted cotton T-shirt", colors=self.colors,
                                   sleeve="long", cuff="plain", fit="slim tapered sleeves")
        loaded = load_skill(Path(__file__).resolve().parents[2] / "apparel-style-recolor")
        self.skill = Skill(self.root, {self.garment.slug: self.garment},
                           loaded.prompt_template, loaded.negative_prompt, 2_000_000)
        self.serial = 0

    def make_runner(self, *, choice="auto", mode="single_pass", sources=None,
                    capacity=3, colors=None, **params):
        self.serial += 1
        root = self.root / f"job-{self.serial:04d}"
        for index, source in enumerate(sources or [self.worn], 1):
            target = root / "inputs" / f"item-{index:02d}" / "view-01.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(source.read_bytes())
        job = Job(f"job-{self.serial:04d}", {"mode": mode, "subject": choice, "subject_choice": choice,
                  "garment_type": self.garment.slug, "provider": "offline-person", "model": "fixture-image",
                  "max_retries": 1, "concurrency": 1, **params}, root=root)
        provider = RecordingProvider(capacity, self.empty.read_bytes())
        runner = JobRunner(job, self.skill, self.garment, colors or self.colors, provider, data_dir=self.root)
        runner.plan()
        return runner, provider

    def reload(self, runner):
        runner.job.save()
        loaded = Job.load(runner.job.path())
        provider = RecordingProvider(runner.provider.max_images, self.empty.read_bytes())
        return JobRunner(loaded, self.skill, self.garment, runner.colors, provider, data_dir=self.root), provider

    def bench(self, provider):
        bench = Workbench.__new__(Workbench)
        bench.config = {"defaults": {"rework": {"refiner": "template"}}}
        bench.env = {}
        bench.data_dir = self.root
        bench.jobs_dir = self.root / "jobs"
        bench.jobs_dir.mkdir(exist_ok=True)
        bench.jobs = {}
        bench.skill = self.skill
        bench.get_provider = lambda name=None: provider
        bench.pricer = SimpleNamespace(estimate=lambda *args: {"count": args[1]})
        bench._quota_guard = lambda *args: None
        bench.gate_for = lambda *args: None
        return bench

    def assert_person(self, request):
        self.assertTrue(request.prompt.startswith(prompts.PERSON_CLAUSE))
        self.assertEqual(request.prompt.count(prompts.PERSON_CLAUSE), 1)
        self.assertIn(prompts.PRESERVE_CLAUSE, request.prompt)
        self.assertEqual(request.prompt.count(prompts.COMPOSITION_CLAUSE), 1)
        self.assertTrue(request.negative.startswith(prompts.NEGATIVE_PERSON))
        for text in (request.prompt, request.negative):
            for forbidden in (prompts.NO_PERSON_CLAUSE, prompts.NEGATIVE_NO_PERSON,
                              "REMOVE the original wearer", "remove any carrier", "no skin anywhere",
                              "the same empty garment exactly as it is", "where the source shows only the garment"):
                self.assertNotIn(forbidden, text)
        self.assertIn("ORIGINAL UPLOAD alone defines the wearer count", request.prompt)
        self.assertIn("restore that same person from an attached original", request.prompt)

    def run_rework(self, bench, source, tasks=None, **kwargs):
        bench.jobs[source.id] = source
        job = asyncio.run(bench.create_rework_job(source.id, [t.id for t in (tasks or source.tasks)],
                         "保持图案，只改衣服颜色", **kwargs))
        runner = JobRunner(job, self.skill, self.garment, self.colors, bench.get_provider(), data_dir=self.root)
        asyncio.run(runner._run_rework())
        self.assertTrue(all(t.status == "done" and t.attempts == 1 for t in job.tasks))
        return job, runner

    def covered(self, variant=0):
        """A worn hood/back, gloves and trousers with no exposed skin at all."""
        path = self.root / f"covered-back-{variant}.png"
        image = Image.new("RGB", (280, 360), (96, 96, 96))
        draw = ImageDraw.Draw(image)
        for x in range(0, 280, 14):
            draw.line((x, 0, x + 70, 360), fill=(70, 70, 70), width=3)
        shade = 36 + variant * 3
        draw.ellipse((95, -20, 185, 80), fill=(shade,) * 3)  # hood, head fully covered
        draw.polygon([(90, 55), (190, 55), (225, 230), (55, 230)], fill=(shade,) * 3)
        draw.rectangle((85, 226, 137, 359), fill=(20,) * 3)
        draw.rectangle((143, 226, 195, 359), fill=(20,) * 3)
        draw.text((100, 130), f"BACK {variant}", fill=(210,) * 3)
        image.save(path, compress_level=0)
        return path

    def assert_auto(self, request):
        self.assertTrue(request.prompt.startswith(prompts.SUBJECT_CLAUSE))
        self.assertEqual(request.prompt.count(prompts.SUBJECT_CLAUSE), 1)
        self.assertIn("NO skin, face or hands are visible", request.prompt)
        self.assertIn("original background", request.prompt)
        self.assertIn("keep those clothes empty", request.prompt)
        self.assertIn(prompts.COMPOSITION_CLAUSE, request.prompt)
        self.assertTrue(request.negative.startswith(prompts.NEGATIVE_SUBJECT))
        for text in (request.prompt, request.negative):
            for forbidden in (prompts.NO_PERSON_CLAUSE, prompts.NEGATIVE_NO_PERSON,
                              "REMOVE the original wearer", "remove any carrier", "no skin anywhere"):
                self.assertNotIn(forbidden, text)

    def test_no_skin_cannot_establish_empty_clothes_including_grayscale_and_missing_file(self):
        hood = self.covered()
        gray = self.root / "grayscale-person.png"
        with Image.open(self.worn) as image:
            image.convert("L").save(gray)
        for source in (hood, gray, self.empty, self.root / "unreadable.png"):
            self.assertEqual(subject.measure(source)["skin"], 0)
            self.assertEqual(subject.detect(source), "auto")
            self.assertEqual(subject.detect(source, "garment"), "garment")
            self.assertEqual(subject.detect(source, "person"), "person")

    def test_six_covered_uploads_three_colors_keep_source_in_all_eighteen_requests(self):
        sources = [self.covered(i) for i in range(6)]
        runner, provider = self.make_runner(sources=sources, colors=self.colors[:3])
        self.assertEqual(len(runner.job.tasks), 18)
        self.assertEqual(runner.job.params["subjects"], {"auto": 6, "garment": 0, "person": 0})
        self.assertTrue(all(t.subject == "auto" for t in runner.job.tasks))
        asyncio.run(runner._run_single_pass())
        self.assertEqual(len(provider.requests), 18)
        originals = {p.read_bytes() for p in sources}
        derived = 0
        for request, payload in zip(provider.requests, provider.payloads):
            self.assert_auto(request)
            if len(payload) == 3:
                derived += 1
                self.assertIn(payload[2], originals)
                self.assertIn("actual wearer presence", request.prompt)
                self.assertIn("IMAGE 3 is the customer's ORIGINAL UPLOAD", request.prompt)
            else:
                self.assertIn(payload[0], originals)
        self.assertEqual(derived, 12)
        self.assertTrue(all(t.status == "done" and t.attempts == 1 for t in runner.job.tasks))

    def test_covered_wearer_recolor_only_and_two_phase_keep_original_without_extra_calls(self):
        for mode in ("recolor_only", "two_phase"):
            original = self.covered()
            runner, provider = self.make_runner(mode=mode, sources=[original], colors=self.colors[:2])
            asyncio.run(runner._run_two_phase() if mode == "two_phase" else runner._run_single_pass())
            self.assertEqual(len(provider.requests), 3 if mode == "two_phase" else 2)
            for request in provider.requests:
                self.assert_auto(request)
            for request, payload in zip(provider.requests[1:], provider.payloads[1:]):
                self.assertIn(original.read_bytes(), payload)

    def test_legacy_auto_garment_decisions_are_rechecked_from_original_on_retry(self):
        runner, _ = self.make_runner(sources=[self.covered()], colors=self.colors[:3])
        runner.job.params.pop("subject_policy")
        runner.job.params["subject"] = "garment"
        for task in runner.job.tasks:
            task.subject = "garment"
            task.status = "failed"
            task.prompt = prompts.build_master_recolor(self.colors[0], garment=self.garment, subject="garment")
            runner.job.params["task_subjects"][task.id] = "garment"
        runner, provider = self.reload(runner)
        runner._retarget_failed()
        asyncio.run(runner._run_single_pass())
        self.assertEqual(len(provider.requests), 3)
        self.assertTrue(all(t.subject == "auto" for t in runner.job.tasks))
        for request in provider.requests:
            self.assert_auto(request)

    def test_legacy_covered_auto_rework_restores_original_role_not_white_current_scene(self):
        original = self.covered()
        runner, provider = self.make_runner(sources=[original], colors=self.colors[:1])
        asyncio.run(runner._run_single_pass())  # deliberately returns the empty white fixture
        runner.job.params.pop("subject_policy")
        runner.job.params["subject"] = "garment"
        runner.job.tasks[0].subject = "garment"
        runner.job.params["task_subjects"][runner.job.tasks[0].id] = "garment"
        runner.job.tasks[0].prompt = prompts.NO_PERSON_CLAUSE
        bench = self.bench(provider)
        job, _ = self.run_rework(bench, runner.job, subject="auto")
        self.assertEqual(job.tasks[0].subject, "auto")
        self.assert_auto(provider.requests[-1])
        self.assertEqual(provider.payloads[-1], [self.empty.read_bytes(), original.read_bytes()])
        self.assertIn("IMAGE 2 determines the ORIGINAL subject presentation", provider.requests[-1].prompt)
        bench.jobs.pop(runner.job.id)
        second, _ = self.run_rework(bench, job, subject="auto")
        self.assertEqual(second.tasks[0].subject, "auto")
        self.assert_auto(provider.requests[-1])
        self.assertEqual(provider.payloads[-1][-1], original.read_bytes())

    def test_auto_keeps_inherited_explicit_empty_choice_when_original_had_a_wearer(self):
        runner, provider = self.make_runner(choice="garment", sources=[self.covered()], colors=self.colors[:1])
        asyncio.run(runner._run_single_pass())
        bench = self.bench(provider)
        first, _ = self.run_rework(bench, runner.job, subject="auto")
        self.assertEqual(first.tasks[0].subject, "garment")
        bench.jobs.pop(runner.job.id)
        second, _ = self.run_rework(bench, first, subject="auto")
        self.assertEqual(second.tasks[0].subject, "garment")
        self.assertTrue(provider.requests[-1].prompt.startswith(prompts.NO_PERSON_CLAUSE))

    def test_auto_rule_and_reference_rebinding_are_idempotent_without_dropping_artwork_lock(self):
        for source in (prompts.NO_PERSON_CLAUSE, prompts._LEGACY_SUBJECT_CLAUSE, prompts.EFFECT_CLAUSE):
            text = source + " " + prompts.PRINT_LOCK_CLAUSE
            prompt = prompts.enforce_composition_rule(prompts.enforce_subject_rule(text, "auto"))
            negative = prompts.enforce_subject_negative(prompts.NEGATIVE_NO_PERSON, "auto")
            self.assert_auto(EditRequest(images=[self.covered()], prompt=prompt, negative=negative))
            self.assertIn(prompts.PRINT_LOCK_CLAUSE, prompt)
            self.assertEqual(prompts.enforce_subject_rule(prompt, "auto"), prompt)
            self.assertEqual(prompts.enforce_composition_rule(prompt), prompt)
            self.assertEqual(prompts.enforce_subject_negative(negative, "auto"), negative)
            rebound = prompts.build_rework_reference_prompt(prompt, "auto", original_count=1)
            self.assertEqual(prompts.build_rework_reference_prompt(rebound, "auto", 1), rebound)

    def test_person_builders_all_pattern_and_reference_paths(self):
        for effect in (False, True):
            for mode in prompts.PATTERN_MODE_KEYS:
                texts = [prompts.build_single_pass(self.skill, self.garment, self.colors[0],
                                                  subject="person", effect=effect, pattern_mode=mode),
                         prompts.build_style_only(self.garment, subject="person", effect=effect),
                         prompts.build_recolor_only(self.colors[0], garment=self.garment,
                                                  subject="person", effect=effect, pattern_mode=mode),
                         prompts.build_master_recolor(self.colors[0], garment=self.garment,
                                                    subject="person", effect=effect)]
                for text in texts:
                    negative = prompts.full_negative(self.skill, mode, self.garment, "person")
                    self.assert_person(EditRequest(images=[self.worn], prompt=text, negative=negative))
                    self.assertEqual(prompts.enforce_subject_rule(text, "person"), text)
                    self.assertEqual(prompts.enforce_composition_rule(text), text)
                    self.assertIn(prompts.MATERIAL_CLAUSE, text)
                if mode != "preserve":
                    self.assertNotIn(prompts.PRINT_LOCK_CLAUSE, texts[0])
                    self.assertNotIn(prompts.PRINT_LOCK_CLAUSE, texts[2])

    def test_single_and_recolor_independent_requests_keep_subject_and_reference_bytes(self):
        for mode in ("single_pass", "recolor_only"):
            for choice in ("auto", "person"):
                runner, provider = self.make_runner(mode=mode, choice=choice, shape_lock="off")
                original = runner.root / runner.job.tasks[0].source
                before = original.read_bytes()
                asyncio.run(runner._run_single_pass())
                self.assertEqual(len(provider.requests), len(self.colors))
                for color, request, payload in zip(self.colors, provider.requests, provider.payloads):
                    self.assert_person(request)
                    self.assertEqual(request.images, [original, color.effect])
                    self.assertEqual(payload, [before, color.effect.read_bytes()])
                self.assertTrue(all(t.subject == "person" and t.attempts == 1 for t in runner.job.tasks))
                self.assertFalse(runner.retry_on_person)

    def test_master_derivation_restores_person_from_true_original_without_new_calls(self):
        for mode in ("single_pass", "recolor_only"):
            runner, provider = self.make_runner(mode=mode)
            asyncio.run(runner._run_single_pass())
            master = runner.job.tasks[0]
            original = (runner.root / master.source).resolve()
            self.assertEqual(len(provider.requests), len(self.colors))
            self.assertEqual((runner.root / master.out).read_bytes(), self.empty.read_bytes())
            for index, request in enumerate(provider.requests):
                self.assert_person(request)
                if index:
                    # 派生色第 2 张改成「面料色卡」（只带颜色、不带版型），原图人物仍是第 3 张
                    images = [Path(x) for x in request.images]
                    self.assertEqual(images[0], runner.root / master.out)
                    self.assertIn("swatches", str(images[1].parent))
                    self.assertEqual(images[2], original)
                    self.assertEqual(provider.payloads[index][2], self.worn.read_bytes())
                    self.assertIn("IMAGE 3 is the customer's ORIGINAL UPLOAD", request.prompt)
                    self.assertIn("This original also controls wearer count", request.prompt)
            self.assertEqual(original.read_bytes(), self.worn.read_bytes())

    def test_person_two_phase_original_attached_and_planned_call_count(self):
        runner, provider = self.make_runner(mode="two_phase")
        asyncio.run(runner._run_two_phase())
        self.assertEqual(len(provider.requests), 1 + len(self.colors))
        self.assertEqual(provider.requests[0].images, [runner.root / runner.job.tasks[0].source])
        for request, payload in zip(provider.requests, provider.payloads):
            self.assert_person(request)
            if len(request.images) == 3:
                self.assertEqual(payload[2], self.worn.read_bytes())
        self.assertTrue(all(t.status == "done" and t.subject == "person" for t in runner.job.tasks))

    def test_capacity_and_disabled_reference_roles_are_truthful(self):
        for capacity, send_reference in ((1, True), (2, True), (2, False), (3, True)):
            runner, provider = self.make_runner(capacity=capacity, send_reference=send_reference)
            asyncio.run(runner._run_single_pass())
            derived = provider.requests[1]
            self.assert_person(derived)
            self.assertLessEqual(len(derived.images), capacity)
            has_original = capacity > 2 or (capacity > 1 and not send_reference)
            self.assertEqual("ORIGINAL UPLOAD REFERENCE" in derived.prompt, has_original)
            if has_original:
                self.assertEqual(provider.payloads[1][-1], self.worn.read_bytes())
            if capacity < 3:
                self.assertNotIn("IMAGE 3", derived.prompt)

    def test_color_swatch_stays_before_person_original(self):
        colors = [replace(c, effect=None) for c in self.colors]
        runner, provider = self.make_runner(colors=colors)
        asyncio.run(runner._run_single_pass())
        for index, request in enumerate(provider.requests[1:], 1):
            self.assert_person(request)
            payloads = provider.payloads[index]
            # 第 2 张是「只做颜色」的参考（色卡 / 颜色参考图），原图人物永远排在最后
            self.assertGreaterEqual(len(payloads), 2)
            self.assertEqual(payloads[-1], self.worn.read_bytes())
            second = payloads[1]
            self.assertIn(second, {colors[index].path.read_bytes(),
                                   payloads[1]})  # 允许色卡（派生色改发面料色卡）

    def test_final_boundary_repairs_stale_garment_prompt_and_negative_for_person(self):
        runner, provider = self.make_runner(choice="person", colors=self.colors[:1])
        task = runner.job.tasks[0]
        task.prompt = prompts.NO_PERSON_CLAUSE
        request = EditRequest(images=[self.worn], prompt=prompts.NO_PERSON_CLAUSE,
                              negative=prompts.NEGATIVE_NO_PERSON)
        asyncio.run(runner._guarded_call(request, task))
        self.assertEqual(len(provider.requests), 1)
        self.assert_person(provider.requests[0])

    def test_explicit_garment_still_beats_person_saved_prompt(self):
        runner, provider = self.make_runner(choice="garment", colors=self.colors[:1])
        task = runner.job.tasks[0]
        task.subject = "person"
        task.prompt = prompts.PERSON_CLAUSE
        asyncio.run(runner._execute_color_task(task))
        request = provider.requests[0]
        self.assertEqual(task.subject, "garment")
        self.assertTrue(request.prompt.startswith(prompts.NO_PERSON_CLAUSE))
        self.assertTrue(request.negative.startswith(prompts.NEGATIVE_NO_PERSON))
        self.assertNotIn(prompts.PERSON_CLAUSE, request.prompt)

    def test_mixed_auto_reload_and_retry_retain_original_task_decisions(self):
        runner, _ = self.make_runner(sources=[self.empty, self.worn])
        before = {t.id: t.subject for t in runner.job.tasks}
        self.assertEqual(set(before.values()), {"auto", "person"})
        self.assertEqual(runner.job.params["subject"], "auto")  # mixed summary is not a choice
        self.assertEqual(runner.job.params["subject_choice"], "auto")
        for task in runner.job.tasks:
            task.status = "failed"
        runner, provider = self.reload(runner)
        runner._retarget_failed()
        with patch("skillbridge.subject.detect", side_effect=AssertionError("Do not reclassify originals")):
            asyncio.run(runner._run_single_pass())
        self.assertEqual({t.id: t.subject for t in runner.job.tasks}, before)
        self.assertEqual(len(provider.requests), len(before))
        for task in runner.job.tasks:
            self.assertIn(prompts.PERSON_CLAUSE if before[task.id] == "person" else prompts.SUBJECT_CLAUSE, task.prompt)

    def test_legacy_mixed_summary_never_becomes_explicit_override(self):
        runner, _ = self.make_runner(sources=[self.empty, self.worn], colors=self.colors[:1])
        before = {t.id: t.subject for t in runner.job.tasks}
        runner.job.params.pop("subject_choice")
        runner.job.params.pop("task_subjects")
        runner, provider = self.reload(runner)
        self.assertEqual(runner.subject_override, "auto")
        asyncio.run(runner._run_single_pass())
        self.assertEqual({t.id: t.subject for t in runner.job.tasks}, before)
        self.assertEqual(len(provider.requests), 2)

    def test_auto_empty_original_remains_empty_by_original_rule_not_skin_assumption(self):
        runner, provider = self.make_runner(sources=[self.empty])
        asyncio.run(runner._run_single_pass())
        self.assertTrue(all(t.subject == "auto" for t in runner.job.tasks))
        for request in provider.requests:
            self.assert_auto(request)
            self.assertIn("keep those clothes empty", request.prompt)

    def test_service_create_and_reload_keep_explicit_choice_separate(self):
        provider = RecordingProvider(3, self.empty.read_bytes())
        bench = self.bench(provider)
        job = bench.create_job([("item-01.png", self.empty.read_bytes()), ("item-02.png", self.worn.read_bytes())],
                               garment_type=self.garment.slug, subject="auto")
        self.assertEqual(job.params["subject_choice"], "auto")
        self.assertEqual(job.params["subject"], "auto")
        loaded = Job.load(job.path())
        self.assertEqual(loaded.params["task_subjects"], {t.id: t.subject for t in loaded.tasks})
        self.assertEqual({t.subject for t in loaded.tasks}, {"auto", "person"})

    def test_auto_rework_inherits_person_despite_damaged_output_and_cross_model(self):
        runner, provider = self.make_runner(colors=self.colors[:1])
        asyncio.run(runner._run_single_pass())
        bench = self.bench(provider)
        job, _ = self.run_rework(bench, runner.job, subject="auto", model="other-image-model")
        self.assertEqual(job.params["subject_choice"], "auto")
        self.assertEqual(job.params["subject"], "person")
        self.assertEqual(job.params["task_subjects"], {job.tasks[0].id: "person"})
        request = provider.requests[-1]
        self.assert_person(request)
        self.assertIn("CROSS-MODEL HANDOFF", request.prompt)
        self.assertNotIn("empty garment if it does not", request.prompt)
        self.assertEqual(provider.payloads[-1], [self.empty.read_bytes(), self.worn.read_bytes()])
        self.assertIn("IMAGE 2 is also authoritative for the original wearer", request.prompt)
        self.assertEqual(len(provider.requests), 2)

    def test_legacy_rework_auto_recovers_original_person_before_wrong_saved_garment(self):
        runner, provider = self.make_runner(colors=self.colors[:1])
        asyncio.run(runner._run_single_pass())
        bench = self.bench(provider)
        bench.jobs[runner.job.id] = runner.job
        legacy_root = bench.jobs_dir / "legacy-empty"
        current = legacy_root / "inputs" / "result.png"
        current.parent.mkdir(parents=True)
        current.write_bytes(self.empty.read_bytes())
        legacy = Job("legacy-empty", {"mode": "rework", "garment_type": self.garment.slug,
                     "subject": "garment", "source_job": runner.job.id,
                     "source_tasks": [runner.job.tasks[0].id]}, root=legacy_root)
        task = replace(runner.job.tasks[0], id="legacy-v2", source="inputs/result.png",
                       out="inputs/result.png", subject="garment", color="white-v2", view="",
                       prompt=prompts.NO_PERSON_CLAUSE, master=False, master_task="")
        legacy.tasks = [task]
        job, _ = self.run_rework(bench, legacy, subject="auto")
        self.assertEqual(job.tasks[0].subject, "person")
        self.assert_person(provider.requests[-1])
        self.assertEqual(provider.payloads[-1][-1], self.worn.read_bytes())

    def test_rework_explicit_overrides_win_in_both_directions(self):
        for initial, requested in (("person", "garment"), ("garment", "person")):
            runner, provider = self.make_runner(choice=initial, colors=self.colors[:1])
            asyncio.run(runner._run_single_pass())
            job, _ = self.run_rework(self.bench(provider), runner.job, subject=requested)
            self.assertEqual(job.tasks[0].subject, requested)
            self.assertEqual(job.params["subject_choice"], requested)
            if requested == "person":
                self.assert_person(provider.requests[-1])
            else:
                self.assertTrue(provider.requests[-1].prompt.startswith(prompts.NO_PERSON_CLAUSE))
                self.assertTrue(provider.requests[-1].negative.startswith(prompts.NEGATIVE_NO_PERSON))

    def test_rework_mixed_auto_keeps_each_task_through_reload(self):
        runner, provider = self.make_runner(sources=[self.empty, self.worn], colors=self.colors[:1])
        asyncio.run(runner._run_single_pass())
        job, rework = self.run_rework(self.bench(provider), runner.job, subject="auto")
        self.assertEqual([t.subject for t in job.tasks], ["auto", "person"])
        rework, provider = self.reload(rework)
        for task in rework.job.tasks:
            task.status = "pending"
        asyncio.run(rework._run_rework())
        self.assertEqual([t.subject for t in rework.job.tasks], ["auto", "person"])
        self.assert_person(provider.requests[1])
        self.assert_auto(provider.requests[0])

    def test_rework_original_copies_survive_parent_removal_and_second_round(self):
        runner, provider = self.make_runner(colors=self.colors[:1])
        asyncio.run(runner._run_single_pass())
        bench = self.bench(provider)
        first, _ = self.run_rework(bench, runner.job, subject="auto")
        bench.jobs.pop(runner.job.id)
        second, rework = self.run_rework(bench, first, subject="auto")
        self.assertEqual(second.tasks[0].subject, "person")
        self.assert_person(provider.requests[-1])
        self.assertEqual(provider.payloads[-1][-1], self.worn.read_bytes())
        self.assertEqual(list(second.params["original_source_meta"].values()),
                         list(first.params["original_source_meta"].values()))
        rework, provider = self.reload(rework)
        self.assertEqual(rework.job.params["subject_choice"], "auto")

    def test_missing_legacy_original_does_not_classify_damaged_current(self):
        provider = RecordingProvider(1, self.empty.read_bytes())
        bench = self.bench(provider)
        root = bench.jobs_dir / "missing-original"
        current = root / "inputs" / "current.png"
        current.parent.mkdir(parents=True)
        current.write_bytes(self.empty.read_bytes())
        job = Job("missing-original", {"mode": "rework", "source_job": "absent",
                  "garment_type": self.garment.slug, "subject": "person"}, root=root)
        task = Task("unknown", "item-01", "", "white-v2", "#fff", "white",
                    source="inputs/current.png", out="inputs/current.png", subject="")
        job.tasks = [task]
        with patch("skillbridge.subject.detect", side_effect=AssertionError("Damaged current is not original evidence")):
            revised, _ = self.run_rework(bench, job, subject="auto")
        self.assertEqual(revised.params["subject"], "auto")
        self.assertEqual(revised.tasks[0].subject, "auto")
        self.assertIn("no original upload is attached", provider.requests[-1].prompt)
        self.assertNotIn(prompts.PERSON_CLAUSE, provider.requests[-1].prompt)
        self.assertNotIn(prompts.NO_PERSON_CLAUSE, provider.requests[-1].prompt)

    def test_legacy_runner_retry_uses_local_original_lineage(self):
        runner, provider = self.make_runner(colors=self.colors[:1])
        asyncio.run(runner._run_single_pass())
        runner.job.save()
        root = self.root / "legacy-retry"
        current = root / "inputs" / "current.png"
        current.parent.mkdir(parents=True)
        current.write_bytes(self.empty.read_bytes())
        job = Job("legacy-retry", {"mode": "rework", "subject": "garment", "source_job": runner.job.id,
                  "source_task_map": {"legacy": runner.job.tasks[0].id}, "max_retries": 1}, root=root)
        task = replace(runner.job.tasks[0], id="legacy", source="inputs/current.png", out=None,
                       subject="auto", status="pending", prompt="Adjust color.")
        job.tasks = [task]
        retry = JobRunner(job, self.skill, self.garment, self.colors, provider, data_dir=self.root)
        asyncio.run(retry._run_rework())
        self.assertEqual(task.subject, "person")
        self.assertEqual(job.params["subject"], task.subject)
        self.assertEqual(job.params["subject_choice"], "auto")
        self.assert_person(provider.requests[-1])
        self.assertIn("IMAGE 2 is also authoritative for the original wearer", provider.requests[-1].prompt)
        self.assertEqual(provider.payloads[-1], [self.empty.read_bytes(), self.worn.read_bytes()])

    def test_person_refine_and_mocked_llm_override_parent_garment_contracts(self):
        parent = prompts.build_single_pass(self.skill, self.garment, self.colors[0], subject="garment")
        for mode in prompts.PATTERN_MODE_KEYS:
            text = refine.compose_template("保持图案", parent, subject="person", pattern_mode=mode)
            self.assert_person(EditRequest(images=[self.worn], prompt=text,
                               negative=prompts.full_negative(self.skill, mode, self.garment, "person")))
        with patch("skillbridge.refine.compose_llm", new=AsyncMock(return_value=prompts.NO_PERSON_CLAUSE)):
            text, used = asyncio.run(refine.compose("换色", parent, subject="person", refiner="llm",
                        cfg={"enabled": True, "model": "fixture", "api_key_env": "TEST"}, env={"TEST": "fake"}))
        self.assertEqual(used, "llm")
        self.assertTrue(text.startswith(prompts.PERSON_CLAUSE))
        self.assertNotIn(prompts.NO_PERSON_CLAUSE, text)

    def test_two_phase_mixed_reload_uses_task_subject_not_fresh_detection(self):
        runner, _ = self.make_runner(mode="two_phase", sources=[self.empty, self.worn], colors=self.colors[:1])
        runner, provider = self.reload(runner)
        with patch.object(runner, "subject_for", side_effect=AssertionError("Keep original task classification")):
            asyncio.run(runner._run_two_phase())
        self.assertEqual(len(provider.requests), 4)
        self.assertEqual([t.subject for t in runner.job.tasks], ["auto", "person"])
        self.assertTrue(all(t.status == "done" for t in runner.job.tasks))
        self.assert_person(provider.requests[1])
        self.assert_person(provider.requests[3])

    def test_ambiguous_or_cyclic_legacy_lineage_does_not_guess_a_person(self):
        runner, _ = self.make_runner(sources=[self.empty, self.worn], colors=self.colors[:1])
        parent = runner.job
        parent.tasks[1].item = parent.tasks[0].item
        parent.tasks[1].view = "view-02"
        task = replace(parent.tasks[0], id="unknown", subject="auto", view="", color="white-v2")
        legacy = Job("legacy-ambiguous", {"mode": "rework", "subject": "person", "source_job": parent.id,
                     "source_tasks": [t.id for t in parent.tasks]}, tasks=[task], root=self.root / "legacy-ambiguous")
        self.assertEqual(subject.task_kind(legacy, task, lambda _: parent), "auto")
        legacy.params.update(source_job=legacy.id, source_task_map={task.id: task.id})
        self.assertEqual(subject.task_kind(legacy, task, lambda _: legacy), "auto")

    def test_person_negative_merge_and_rebound_normalized_roles(self):
        text = prompts.build_single_pass(self.skill, self.garment, self.colors[0], subject="person")
        negative = prompts.full_negative(self.skill, "preserve", self.garment, "person")
        for supported in (True, False):
            merged = prompts.with_negative(text, negative, supported=supported)
            self.assert_person(EditRequest(images=[self.worn], prompt=merged, negative=negative))
        saved = prompts.enforce_subject_rule(prompts.EFFECT_CLAUSE, "garment")
        saved = prompts.enforce_subject_rule(saved, "person")
        rebound = prompts.build_rework_reference_prompt(saved, "person", original_count=1)
        self.assertNotIn("official product photo", rebound)
        self.assertNotIn("RESHAPE, DO NOT RECOLOUR", rebound)
        self.assertIn("IMAGE 2 is also authoritative for the original wearer", rebound)
        self.assertEqual(prompts.build_rework_reference_prompt(rebound, "person", 1), rebound)
        old = prompts.PERSON_CLAUSE + " " + prompts._LEGACY_COMPOSITION_CLAUSE
        self.assertEqual(prompts.enforce_composition_rule(old).count(prompts.COMPOSITION_CLAUSE), 1)
        garment = prompts.enforce_subject_rule(text + " " + prompts.original_upload_reference_clause(3, "person"), "garment")
        self.assertNotIn("This original also controls wearer count", garment)

    def test_rework_person_with_one_image_reports_missing_original_evidence(self):
        runner, provider = self.make_runner(capacity=1, colors=self.colors[:1])
        asyncio.run(runner._run_single_pass())
        revised, _ = self.run_rework(self.bench(provider), runner.job, subject="auto")
        request = provider.requests[-1]
        self.assert_person(request)
        self.assertEqual(len(request.images), 1)
        self.assertIn("no original upload is attached", request.prompt)
        self.assertIn("Original-person identity evidence is unavailable here", request.prompt)
        self.assertNotIn("IMAGE 2", request.prompt)
        self.assertEqual(revised.params["subject"], revised.tasks[0].subject)

    def test_front_back_canvas_and_bytes_preserved_with_person_derivation(self):
        composite = self.root / "worn-front-back.png"
        with Image.open(self.worn) as worn:
            image = Image.new("RGB", (512, 256))
            image.paste(worn, (0, 0))
            image.paste(worn, (256, 0))
        ImageDraw.Draw(image).text((350, 125), "BACK ART", fill="red")
        image.save(composite, compress_level=0)
        runner, provider = self.make_runner(sources=[composite], colors=self.colors[:2])
        asyncio.run(runner._run_single_pass())
        self.assert_person(provider.requests[-1])
        self.assertIn("front AND back together", provider.requests[-1].prompt)
        self.assertEqual(provider.payloads[-1][-1], composite.read_bytes())

    def test_network_guard_blocks_sync_async_http_and_external_socket(self):
        with httpx.Client() as client, self.assertRaises(AssertionError):
            client.get("https://offline-person.invalid/")
        with socket.socket() as sock, self.assertRaises(AssertionError):
            sock.connect(("192.0.2.1", 443))
        async def call():
            async with httpx.AsyncClient() as client:
                await client.get("https://offline-person.invalid/")
        with self.assertRaises(AssertionError):
            asyncio.run(call())


if __name__ == "__main__":
    with no_external_network():
        unittest.main(verbosity=2)
