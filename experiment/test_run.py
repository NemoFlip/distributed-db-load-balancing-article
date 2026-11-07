import copy
import json
from pathlib import Path
import unittest

from run import (Case, Placement, actions, apply_action, changed_vectors,
                 load_vectors, make_case, percentile, queue_penalty, rates_for_epoch,
                 rebalance, request_trace, simulate, validate_config, violations)


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(Path(__file__).with_name('config.json').read_text())

    def test_invalid_replication_is_rejected(self):
        self.config['replication_factor'] = self.config['nodes'] + 1
        with self.assertRaises(ValueError):
            validate_config(self.config)

    def test_initial_replica_and_leader_counts_are_balanced(self):
        case = make_case(self.config, 7, 'uniform')
        rates = rates_for_epoch(self.config, case, 'uniform', 0)
        vectors = load_vectors(case.initial, case, rates, self.config)
        self.assertEqual(vectors[2], [9.0] * 8)
        self.assertEqual(vectors[3], [3.0] * 8)
        self.assertEqual(violations(case.initial, case, self.config), 0)

    def test_incremental_move_load_matches_independent_full_recomputation(self):
        case = make_case(self.config, 11, 'heterogeneous')
        rates = rates_for_epoch(self.config, case, 'heterogeneous', 0)
        vectors = load_vectors(case.initial, case, rates, self.config)
        kinds = set()
        for action in actions(case.initial, case, 0, self.config):
            kinds.add(action[0])
            incremental = changed_vectors(vectors, case.initial, action, case, rates, self.config)
            recomputed = load_vectors(apply_action(case.initial, action), case, rates, self.config)
            for values, expected in zip(incremental, recomputed):
                for actual, correct in zip(values, expected):
                    self.assertAlmostEqual(actual, correct, places=10)
        self.assertEqual(kinds, {'leader', 'replica'})

    def test_move_budget_and_constraints_hold_after_every_action(self):
        for seed in [0, 5, 11]:
            case = make_case(self.config, seed, 'shift')
            original = copy.deepcopy(case.initial)
            rates = rates_for_epoch(self.config, case, 'shift', 3)
            for strategy in self.config['strategies']:
                placement, moves = rebalance(case.initial, case, rates, strategy, self.config)
                self.assertLessEqual(sum(move['mib'] for move in moves), 512)
                self.assertLessEqual(len(moves), 12)
                current = case.initial
                for move in moves:
                    current = apply_action(current, (move['kind'], move['segment'],
                                                    move['source'], move['target'], move['mib']))
                    self.assertEqual(violations(current, case, self.config), 0)
                    self.assertGreater(move['fitness_before'], move['fitness_after'])
                self.assertEqual(current, placement)
            self.assertEqual(case.initial, original)

    def test_leader_transfer_keeps_replica_placement(self):
        case = make_case(self.config, 4, 'uniform')
        action = next(a for a in actions(case.initial, case, 512, self.config) if a[0] == 'leader')
        changed = apply_action(case.initial, action)
        self.assertEqual(action[4], 0)
        self.assertEqual(changed.replicas, case.initial.replicas)
        self.assertNotEqual(changed.leaders, case.initial.leaders)
        self.assertFalse(any(a[0] == 'replica' for a in actions(case.initial, case, 512, self.config)))

    def test_common_arrivals_are_independent_of_node_speeds(self):
        case_a = make_case(self.config, 3, 'zipf')
        case_b = make_case(self.config, 3, 'heterogeneous')
        rates_a = rates_for_epoch(self.config, case_a, 'zipf', 2)
        rates_b = rates_for_epoch(self.config, case_b, 'heterogeneous', 2)
        self.assertEqual(rates_a, rates_b)
        self.assertEqual(request_trace(self.config, case_a, rates_a, 3, 'zipf', 2),
                         request_trace(self.config, case_b, rates_b, 3, 'heterogeneous', 2))
        self.assertNotEqual(case_a.speeds, case_b.speeds)

    def test_fcfs_queue_matches_hand_calculation_and_retains_late_completions(self):
        case = Case((64,), (0.0,), (1.0,), 64, Placement(((0,),), (0,)), (0,))
        self.config.update({'nodes': 1, 'warmup_seconds': 0.0})
        result = simulate([(0.0, 0, False), (0.001, 0, False)], case.initial, case, self.config)
        self.assertEqual(result['operations'], 2)
        self.assertAlmostEqual(result['mean_ms'], 5.5)
        self.assertAlmostEqual(result['p95_ms'], 7.0)
        self.config['warmup_seconds'] = 0.0005
        result = simulate([(0.0, 0, False), (0.001, 0, False)], case.initial, case, self.config)
        self.assertEqual(result['operations'], 1)
        self.assertAlmostEqual(result['mean_ms'], 7.0)

    def test_write_produces_distinct_leader_and_follower_operations(self):
        case = Case((64,), (1.0,), (1.0, 1.0, 1.0), 64,
                    Placement(((0, 1, 2),), (0,)), (0,))
        self.config.update({'nodes': 3, 'warmup_seconds': 0.0})
        result = simulate([(0.0, 0, True)], case.initial, case, self.config)
        self.assertEqual(result['operations'], 3)
        self.assertAlmostEqual(result['mean_ms'], (12 + 8 + 8) / 3)
        self.assertAlmostEqual(result['p95_ms'], 12.0)

    def test_empty_measurement_is_an_error(self):
        case = make_case(self.config, 1, 'uniform')
        with self.assertRaises(ValueError):
            simulate([], case.initial, case, self.config)

    def test_percentile_definition_and_penalty_continuity(self):
        self.assertEqual(percentile([1, 2, 3, 4], 0.5), 2)
        self.assertEqual(percentile([1, 2, 3, 4], 0.95), 4)
        self.assertAlmostEqual(queue_penalty(0.95), 19.0)
        self.assertAlmostEqual(queue_penalty(0.951), 19.4)
        self.assertGreater(queue_penalty(1.1), queue_penalty(1.0))


if __name__ == '__main__':
    unittest.main()
