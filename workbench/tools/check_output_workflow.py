"""Offline output workflow tests; no credentials, providers or external network.

Run: python -B workbench/tools/check_output_workflow.py -q
"""
from __future__ import annotations
import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_person_consistency as fixtures
from skillbridge import prompts, subject, output_policy
from skillbridge.job import Job
from skillbridge.packaging import package_outputs
from skillbridge.providers.base import ProviderError


class OutputWorkflowChecks(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.PersonConsistencyChecks('runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def test_targeted_person_edit_recognition_and_negation(self):
        for text in ('只保留男模特，去掉女模特', '删除其中一个模特', 'keep only the man', 'remove the woman'):
            self.assertTrue(output_policy.edits_people(text), text)
        for text in ('不要移除模特', '不要加人物', '图案不动，背景不动', 'remove the logo', 'do not remove the woman'):
            self.assertFalse(output_policy.edits_people(text), text)

    def test_targeted_removal_survives_all_colors_without_restoring_removed_person(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:3],
            extra_requirements='只保留原图男模特，去掉女模特，保留男模特头部和双腿，背景不动')
        asyncio.run(runner._run_single_pass())
        self.assertEqual(len(provider.requests), 3)
        for request in provider.requests:
            self.assertTrue(request.prompt.startswith('TARGETED PERSON EDIT'))
            self.assertNotIn(prompts.PERSON_CLAUSE, request.prompt)
            self.assertNotIn('Preserve every original person', request.prompt)
            self.assertNotIn('This original also controls wearer count', request.prompt)
            self.assertNotIn('Do not remove, erase, replace, duplicate or invent the original wearer;', request.negative)
            self.assertIn('never delete the head', request.prompt)
            self.assertIn('Do not reintroduce a deliberately removed person', request.prompt)
            self.assertIn(prompts.PRINT_LOCK_CLAUSE, request.prompt)
        self.assertIn('ORIGINAL ARTWORK DETAIL AUTHORITY', provider.requests[1].prompt)
        self.assertEqual(provider.payloads[1][-1], self.fx.worn.read_bytes())

    def test_rework_keeps_previously_approved_targeted_removal(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:1],
            extra_requirements='只保留男模特，去掉女模特')
        asyncio.run(runner._run_single_pass())
        job, _ = self.fx.run_rework(self.fx.bench(provider), runner.job, subject='auto')
        self.assertIn('只保留男模特', job.params['subject_edit'])
        self.assertTrue(provider.requests[-1].prompt.startswith('TARGETED PERSON EDIT'))
        self.assertIn('Do not reintroduce a deliberately removed person', provider.requests[-1].prompt)

    def test_default_body_and_artwork_preservation_not_unlocked_by_negative_person_edit(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:2],
            extra_requirements='不要移除模特，图案不动，头和腿完整保留')
        asyncio.run(runner._run_single_pass())
        for request in provider.requests:
            self.fx.assert_person(request)
            self.assertIn('entire originally visible head', request.prompt)
            self.assertIn('trousers, legs and feet', request.prompt)
        self.assertIn('Never inherit blurred', provider.requests[-1].prompt)

    def test_background_edit_unlocked_only_for_explicit_request(self):
        for kind in ('person', 'auto'):
            base = prompts.enforce_composition_rule(prompts.enforce_subject_rule('Keep artwork.', kind))
            negative = prompts.enforce_subject_negative('', kind)
            changed, neg = prompts.apply_output_policy(base, negative, kind, '把背景换成蓝色')
            self.assertTrue(changed.startswith('EXPLICIT BACKGROUND EDIT'))
            self.assertNotIn("Do not change the original upload's wearer presence, worn/flat-lay presentation, pose or background.", neg)
            self.assertIn('all retained subject/anatomy/artwork locks still apply', changed)
            same, _ = prompts.apply_output_policy(base, negative, kind, '背景不动，图案不动')
            self.assertEqual(same, base)

    def test_legacy_auto_rework_cannot_override_reachable_explicit_garment_choice(self):
        runner, _ = self.fx.make_runner(choice='garment', colors=self.fx.colors[:1])
        task = replace(runner.job.tasks[0], id='legacy-task', subject='person')
        legacy = Job('legacy', {'subject_choice':'auto','subject':'person','mode':'rework',
            'source_job':runner.job.id,'source_task_map':{task.id:runner.job.tasks[0].id},
            'task_subjects':{task.id:'person'}}, tasks=[task], root=self.fx.root/'legacy')
        self.assertEqual(subject.task_kind(legacy, task, lambda _:runner.job), 'garment')
        self.assertEqual(subject.task_kind(legacy, task, lambda _:(_ for _ in ()).throw(KeyError())), 'person')

    def test_retry_warning_then_success_reaches_terminal_zip_state(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:2], max_retries=2)
        runner.skill.root = Path(__file__).resolve().parents[2]/'apparel-style-recolor'
        provider.open = AsyncMock()
        original_edit = provider.edit
        count = 0
        async def edit(request, timeout):
            nonlocal count
            count += 1
            if count == 2:
                raise ProviderError('Server disconnected without sending a response.', retryable=True)
            return await original_edit(request, timeout)
        provider.edit = edit
        asyncio.run(runner.run())
        self.assertEqual(runner.job.status, 'done')
        self.assertEqual(runner.job.counts()['done'], 2)
        self.assertEqual(runner.job.tasks[1].attempts, 2)
        self.assertIsNone(runner.job.tasks[1].error)
        self.assertIn('generation_finished_at', runner.job.params)
        self.assertIn('packaging_progress', runner.job.params)
        self.assertTrue((runner.root/runner.job.zip_rel).is_file())
        with zipfile.ZipFile(runner.root/runner.job.zip_rel) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), 2)

    def test_packaging_timeout_reaps_child_and_preserves_sources(self):
        script = self.fx.root/'slow.py'
        script.write_text("import time\nprint('PACKAGING 0/1 waiting', flush=True)\ntime.sleep(30)\n", encoding='utf-8')
        progress = []
        result = asyncio.run(package_outputs(script=script, staging_dir=self.fx.root,
            output_dir=self.fx.root/'out', batch_name='batch', max_bytes=2000000,
            on_progress=progress.append, timeout_sec=.25))
        self.assertFalse(result.ok)
        self.assertIn('超时', result.error)
        self.assertTrue(progress)
        self.assertTrue(self.fx.worn.is_file())

    def test_cancel_during_packaging_finishes_without_running_job_or_losing_images(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:1])
        provider.open = AsyncMock()
        async def cancel_package():
            raise asyncio.CancelledError()
        runner._package = cancel_package
        async def run():
            try:
                await runner.run()
            except asyncio.CancelledError:
                pass
        asyncio.run(run())
        self.assertEqual(runner.job.status, 'partial')
        self.assertIn('打包已取消', runner.job.stage)
        self.assertEqual(runner.job.counts()['done'], 1)
        self.assertTrue((runner.root/runner.job.tasks[0].out).is_file())

    def test_removed_subjects_are_excluded_from_all_reference_and_negative_view_locks(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:2], extra_requirements='去掉女模特，只保留男模特')
        asyncio.run(runner._run_single_pass())
        for request in provider.requests:
            for text in (request.prompt, request.negative):
                self.assertNotIn('ALL original garments/views', text)
                self.assertNotIn('original garment/view count', text)
                self.assertNotIn('ALL views from the ORIGINAL UPLOAD', text)
            self.assertIn('Explicitly removed people and their garments are excluded', request.negative)

    def test_person_selection_does_not_persist_old_background_or_color(self):
        runner, _ = self.fx.make_runner(colors=self.fx.colors[:1], extra_requirements='只保留男模特，去掉女模特，背景换成蓝色，衣服换成红色')
        self.assertIn('男模特', runner.job.params['subject_edit'])
        self.assertNotIn('蓝色', runner.job.params['subject_edit'])
        self.assertNotIn('红色', runner.job.params['subject_edit'])
        self.assertNotIn('背景', runner.job.params['subject_edit'])

    def test_negative_background_requests_never_unlock_scene(self):
        for text in ('不要更换背景', '背景不要换成白色', 'do not change the background', "don't replace the background", '背景不动'):
            self.assertFalse(output_policy.edits_background(text), text)

    def test_explicit_background_change_removes_full_person_and_master_scene_locks(self):
        text = prompts.PERSON_CLAUSE + ' ' + prompts.PRESERVE_CLAUSE + ' ' + prompts.MASTER_EFFECT_CLAUSE
        changed, _ = prompts.apply_output_policy(text, prompts.NEGATIVE_PERSON, 'person', '把背景换成蓝色')
        self.assertNotIn('background, bedding, wall, props', changed)
        self.assertNotIn('background and lighting exactly as they are', changed)
        self.assertIn('trousers, legs and feet', changed)



if __name__ == '__main__':
    unittest.main(verbosity=2)