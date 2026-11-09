"""Offline contracts for printed-ink shapes vs cloth negative space.
No vision or automatic pixel repair is claimed; captured requests and unchanged
input/output bytes are verified with synthetic sources and blocked network.
"""
from __future__ import annotations
import asyncio
from pathlib import Path
import sys
import unittest
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_person_consistency as fixtures
from skillbridge import prompts
from skillbridge.providers.base import EditRequest


class ArtworkTopologyChecks(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.PersonConsistencyChecks('runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def assert_ink_and_cloth(self, request):
        self.assertEqual(request.prompt.count(prompts.ARTWORK_TOPOLOGY_CLAUSE), 1)
        self.assertEqual(request.negative.count(prompts.NEGATIVE_ARTWORK_TOPOLOGY), 1)
        self.assertIn("between a printed character's legs", request.prompt)
        self.assertIn("this task's target fabric colour", request.prompt)
        self.assertIn('Preserve genuine black ink', request.prompt)
        self.assertIn('including boots, eyes, antlers, outlines and lettering', request.prompt)
        self.assertIn("master's pixel lock does not protect", request.prompt)
        self.assertIn('No new black matte', request.negative)

    def test_positive_builders_and_negative_preserve_ink_and_fabric_roles(self):
        for kind in ('auto', 'person', 'garment'):
            color = self.fx.colors[0]
            texts = [prompts.build_single_pass(self.fx.skill, self.fx.garment, color, subject=kind),
                     prompts.build_recolor_only(color, garment=self.fx.garment, subject=kind),
                     prompts.build_master_recolor(color, garment=self.fx.garment, subject=kind)]
            neg = prompts.full_negative(self.fx.skill, 'preserve', self.fx.garment, kind)
            for text in texts:
                self.assert_ink_and_cloth(EditRequest(images=[self.fx.worn], prompt=text, negative=neg))

    def test_final_boundary_is_idempotent_and_does_not_rewrite_target_color(self):
        for label in ('white', 'black', 'pink'):
            prompt = f'Target fabric {label}.'
            first = prompts.enforce_artwork_topology(prompt, '')
            second = prompts.enforce_artwork_topology(*first)
            self.assertEqual(first, second)
            self.assertIn(f'Target fabric {label}', second[0])
            self.assert_ink_and_cloth(EditRequest(images=[self.fx.worn], prompt=second[0], negative=second[1]))

    def test_every_color_uses_original_bytes_and_cannot_inherit_master_black_patch_as_authority(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:3])
        asyncio.run(runner._run_single_pass())
        self.assertEqual(runner.job.params['artwork_policy'], 'ink-and-cloth-v1')
        self.assertEqual(len(provider.requests), 3)
        for request in provider.requests:
            self.assert_ink_and_cloth(request)
            self.fx.assert_person(request)
        for payload in provider.payloads[1:]:
            self.assertEqual(payload[-1], self.fx.worn.read_bytes())
        # No blanket dark-pixel rewrite is applied to input or provider output.
        self.assertEqual((runner.root/runner.job.tasks[0].out).read_bytes(), self.fx.empty.read_bytes())

    def test_old_saved_requests_are_protected_at_actual_send_boundary(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:1])
        task = runner.job.tasks[0]
        task.prompt = 'Legacy saved brief: keep the artwork.'
        asyncio.run(runner._execute_color_task(task))
        self.assert_ink_and_cloth(provider.requests[0])

    def test_two_phase_and_recolor_paths_receive_the_rule(self):
        for mode in ('two_phase', 'recolor_only'):
            runner, provider = self.fx.make_runner(mode=mode, colors=self.fx.colors[:2])
            asyncio.run(runner._run_two_phase() if mode == 'two_phase' else runner._run_single_pass())
            self.assertEqual(len(provider.requests), 3 if mode=='two_phase' else 2)
            for request in provider.requests:
                self.assert_ink_and_cloth(request)

    def test_rework_keeps_current_color_and_original_reference(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:1])
        asyncio.run(runner._run_single_pass())
        revised, _ = self.fx.run_rework(self.fx.bench(provider), runner.job, subject='auto')
        self.assert_ink_and_cloth(provider.requests[-1])
        self.assertEqual(provider.payloads[-1], [self.fx.empty.read_bytes(), self.fx.worn.read_bytes()])
        self.assertIn('do NOT copy original fabric color back', provider.requests[-1].prompt)
        self.assertEqual(revised.params['artwork_policy'], 'ink-and-cloth-v1')

    def test_user_pattern_replacement_and_variants_are_not_frozen_by_new_rule(self):
        for mode in ('replace', 'per_color_variant', 'free'):
            runner, provider = self.fx.make_runner(colors=self.fx.colors[:2], pattern_mode=mode)
            asyncio.run(runner._run_single_pass())
            for request in provider.requests:
                self.assertNotIn(prompts.ARTWORK_TOPOLOGY_CLAUSE, request.prompt)
                self.assertNotIn(prompts.NEGATIVE_ARTWORK_TOPOLOGY, request.negative)
            preview = prompts.apply_requirement(prompts.ARTWORK_TOPOLOGY_CLAUSE,
                '图案换成新的卡通', pattern_mode=mode, subject='person')
            self.assertNotIn(prompts.ARTWORK_TOPOLOGY_CLAUSE, preview)

    def test_selective_person_edit_still_wins_over_artwork_source_recovery(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:2], extra_requirements='只保留男模特，去掉女模特')
        asyncio.run(runner._run_single_pass())
        for request in provider.requests:
            self.assert_ink_and_cloth(request)
            self.assertTrue(request.prompt.startswith('TARGETED PERSON EDIT'))
            self.assertIn('Do not reintroduce a deliberately removed person', request.prompt)

    def test_limited_reference_capacity_never_claims_an_unattached_original_was_recovered(self):
        runner, provider = self.fx.make_runner(colors=self.fx.colors[:2], capacity=2)
        asyncio.run(runner._run_single_pass())
        self.assertEqual(len(provider.requests[-1].images), 2)
        self.assert_ink_and_cloth(provider.requests[-1])
        self.assertIn('Without an attached original', provider.requests[-1].prompt)
        self.assertNotIn("IMAGE 3 is the customer's ORIGINAL UPLOAD", provider.requests[-1].prompt)


if __name__ == '__main__':
    unittest.main(verbosity=2)
