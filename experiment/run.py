"""Reproducible placement experiment. Python 3.9+, standard library only."""

import argparse
from bisect import bisect_left
import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import statistics


@dataclass(frozen=True)
class Placement:
    replicas: tuple
    leaders: tuple


@dataclass(frozen=True)
class Case:
    sizes: tuple
    write_fractions: tuple
    speeds: tuple
    storage_limit: int
    initial: Placement
    ranks: tuple


def validate_config(config):
    if not 1 <= config['replication_factor'] <= config['nodes']:
        raise ValueError('replication_factor must be between 1 and nodes')
    positive = ['segments', 'epochs', 'seed_count', 'request_rate',
                'measurement_seconds', 'migration_budget_mib',
                'read_seconds', 'leader_write_seconds', 'follower_write_seconds']
    if any(config[key] <= 0 for key in positive):
        raise ValueError('counts, rates, service times and budget must be positive')
    if not 0 <= config['write_fraction_min'] <= config['write_fraction_max'] <= 1:
        raise ValueError('invalid write fractions')
    if config['warmup_seconds'] < 0 or config['minimum_improvement'] <= 0:
        raise ValueError('invalid warmup or minimum improvement')
    if config['leader_write_seconds'] < config['follower_write_seconds']:
        raise ValueError('leader write service must not be cheaper than follower service')
    if config['max_actions_per_epoch'] < 0 or not config['segment_sizes_mib']:
        raise ValueError('invalid action limit or sizes')
    if any(size <= 0 for size in config['segment_sizes_mib']):
        raise ValueError('segment sizes must be positive')
    if set(config['scenarios']) - {'uniform', 'zipf', 'shift', 'heterogeneous'}:
        raise ValueError('unknown scenario')
    if set(config['strategies']) - {'count', 'qps', 'work', 'cost'}:
        raise ValueError('unknown strategy')


def make_case(config, seed, scenario):
    rng = random.Random(100000 + seed)
    nodes, segments, factor = (config[key] for key in
                               ['nodes', 'segments', 'replication_factor'])
    sizes = tuple(rng.choice(config['segment_sizes_mib']) for _ in range(segments))
    writes = tuple(rng.uniform(config['write_fraction_min'],
                               config['write_fraction_max']) for _ in range(segments))
    order = list(range(nodes))
    rng.shuffle(order)
    replicas = tuple(tuple(sorted(order[(segment + offset) % nodes]
                                   for offset in range(factor)))
                     for segment in range(segments))
    leaders = tuple(order[segment % nodes] for segment in range(segments))
    ranks = list(range(segments))
    rng.shuffle(ranks)
    speeds = tuple(0.65 + 0.7 * node / (nodes - 1) if nodes > 1 else 1.0
                   for node in range(nodes)) if scenario == 'heterogeneous' else (1.0,) * nodes
    limit = math.ceil(2 * factor * sum(sizes) / nodes / 64) * 64
    case = Case(sizes, writes, speeds, limit, Placement(replicas, leaders), tuple(ranks))
    if violations(case.initial, case, config):
        raise ValueError('initial placement exceeds storage constraints')
    return case


def rates_for_epoch(config, case, scenario, epoch):
    segments = config['segments']
    weights = [1.0] * segments
    if scenario != 'uniform':
        shift = (epoch // 2) * 7 if scenario == 'shift' else 0
        ranks = case.ranks[shift % segments:] + case.ranks[:shift % segments]
        for rank, segment in enumerate(ranks):
            weights[segment] = 1 / (rank + 1) ** config['zipf_alpha']
    total = sum(weights)
    return tuple(config['request_rate'] * value / total for value in weights)


def storage(placement, case, nodes):
    result = [0] * nodes
    for segment, copies in enumerate(placement.replicas):
        for node in copies:
            result[node] += case.sizes[segment]
    return result


def violations(placement, case, config):
    factor, nodes = config['replication_factor'], config['nodes']
    invalid = sum(len(copies) != factor or len(set(copies)) != factor or
                  any(node < 0 or node >= nodes for node in copies) or
                  placement.leaders[segment] not in copies
                  for segment, copies in enumerate(placement.replicas))
    return invalid + sum(size > case.storage_limit for size in storage(placement, case, nodes))


def load_vectors(placement, case, rates, config):
    nodes = config['nodes']
    qps, work, copies, leaders = ([0.0] * nodes for _ in range(4))
    for segment, replicas in enumerate(placement.replicas):
        write_rate = rates[segment] * case.write_fractions[segment]
        read_rate = rates[segment] - write_rate
        for node in replicas:
            qps[node] += write_rate
            work[node] += write_rate * config['follower_write_seconds']
            copies[node] += 1
        leader = placement.leaders[segment]
        qps[leader] += read_rate
        work[leader] += read_rate * config['read_seconds'] + write_rate * (
            config['leader_write_seconds'] - config['follower_write_seconds'])
        leaders[leader] += 1
    return qps, work, copies, leaders


def queue_penalty(rho):
    # Tangent continuation at 0.95; remains finite for overloaded candidates.
    return rho / (1 - rho) if rho <= 0.95 else 19 + 400 * (rho - 0.95)


def fitness(vectors, speeds, strategy, config):
    qps, work, copies, leaders = vectors
    if strategy == 'count':
        return statistics.pstdev(copies) / statistics.mean(copies) + 0.25 * (
            statistics.pstdev(leaders) / statistics.mean(leaders))
    if strategy == 'qps':
        mean_qps = statistics.mean(qps)
        return 0.0 if mean_qps == 0 else (max(qps) + statistics.pstdev(qps)) / mean_qps
    rho = [value / speed for value, speed in zip(work, speeds)]
    return max(rho) + statistics.pstdev(rho) + config['queue_weight'] * (
        sum(queue_penalty(value) for value in rho) / len(rho))


def actions(placement, case, used_mib, config):
    occupied = storage(placement, case, config['nodes'])
    for segment, copies in enumerate(placement.replicas):
        leader = placement.leaders[segment]
        for target in copies:
            if target != leader:
                yield ('leader', segment, leader, target, 0)
        size = case.sizes[segment]
        if used_mib + size > config['migration_budget_mib']:
            continue
        for source in copies:
            for target in range(config['nodes']):
                if target not in copies and occupied[target] + size <= case.storage_limit:
                    yield ('replica', segment, source, target, size)


def apply_action(placement, action):
    kind, segment, source, target, _ = action
    copies = list(placement.replicas)
    leaders = list(placement.leaders)
    if kind == 'replica':
        copies[segment] = tuple(sorted(target if node == source else node
                                       for node in copies[segment]))
    if kind == 'leader' or leaders[segment] == source:
        leaders[segment] = target
    return Placement(tuple(copies), tuple(leaders))


def changed_vectors(vectors, placement, action, case, rates, config):
    kind, segment, source, target, _ = action
    qps, work, copies, leaders = (list(values) for values in vectors)
    write_rate = rates[segment] * case.write_fractions[segment]
    read_rate = rates[segment] - write_rate
    if kind == 'replica':
        qps[source] -= write_rate
        qps[target] += write_rate
        work[source] -= write_rate * config['follower_write_seconds']
        work[target] += write_rate * config['follower_write_seconds']
        copies[source] -= 1
        copies[target] += 1
    if kind == 'leader' or placement.leaders[segment] == source:
        leader_work = read_rate * config['read_seconds'] + write_rate * (
            config['leader_write_seconds'] - config['follower_write_seconds'])
        qps[source] -= read_rate
        qps[target] += read_rate
        work[source] -= leader_work
        work[target] += leader_work
        leaders[source] -= 1
        leaders[target] += 1
    return qps, work, copies, leaders


def rebalance(placement, case, rates, strategy, config):
    moves, used_mib = [], 0
    vectors = load_vectors(placement, case, rates, config)
    for _ in range(config['max_actions_per_epoch']):
        current = fitness(vectors, case.speeds, strategy, config)
        best_score = current - config['minimum_improvement']
        best_action, best_vectors = None, None
        for action in actions(placement, case, used_mib, config):
            candidate_vectors = changed_vectors(vectors, placement, action, case, rates, config)
            score = fitness(candidate_vectors, case.speeds, strategy, config)
            if strategy == 'cost':
                leader_change = action[0] == 'leader' or placement.leaders[action[1]] == action[2]
                score += (config['migration_weight'] * action[4] / config['migration_budget_mib']
                          + config['leader_change_weight'] * leader_change)
            if score < best_score - 1e-12:
                best_score, best_action, best_vectors = score, action, candidate_vectors
        if best_action is None:
            break
        placement = apply_action(placement, best_action)
        vectors = best_vectors
        used_mib += best_action[4]
        moves.append({'kind': best_action[0], 'segment': best_action[1],
                      'source': best_action[2], 'target': best_action[3],
                      'mib': best_action[4], 'fitness_before': current,
                      'fitness_after': fitness(vectors, case.speeds, strategy, config)})
        if violations(placement, case, config):
            raise AssertionError('invalid placement after an accepted action')
    return placement, moves


def request_trace(config, case, rates, seed, scenario, epoch):
    # Heterogeneous and Zipf have identical arrivals; only node speeds differ.
    scenario_code = {'uniform': 1, 'zipf': 2, 'shift': 3, 'heterogeneous': 2}[scenario]
    trace_seed = 1000000 + 1000 * seed + 100 * scenario_code + epoch
    rng = random.Random(trace_seed)
    total = sum(rates)
    cumulative, running = [], 0.0
    for rate in rates:
        running += rate / total
        cumulative.append(running)
    cumulative[-1] = 1.0
    end = config['warmup_seconds'] + config['measurement_seconds']
    result, time = [], 0.0
    while True:
        time += rng.expovariate(total)
        if time >= end:
            break
        segment = min(bisect_left(cumulative, rng.random()), len(rates) - 1)
        result.append((time, segment, rng.random() < case.write_fractions[segment]))
    digest = hashlib.sha256(json.dumps(result, separators=(',', ':')).encode()).hexdigest()
    return result, trace_seed, digest


def percentile(sorted_values, fraction):
    return sorted_values[max(0, math.ceil(fraction * len(sorted_values)) - 1)]


def simulate(trace, placement, case, config):
    available = [0.0] * config['nodes']
    response = []
    for arrival, segment, is_write in trace:
        leader = placement.leaders[segment]
        targets = placement.replicas[segment] if is_write else (leader,)
        for node in targets:
            service = (config['leader_write_seconds'] if node == leader else
                       config['follower_write_seconds']) if is_write else config['read_seconds']
            completion = max(arrival, available[node]) + service / case.speeds[node]
            available[node] = completion
            if arrival >= config['warmup_seconds']:
                response.append(completion - arrival)
    if not response:
        raise ValueError('empty measurement interval')
    response.sort()
    return {'operations': len(response), 'mean_ms': 1000 * statistics.mean(response),
            'p95_ms': 1000 * percentile(response, 0.95),
            'p99_ms': 1000 * percentile(response, 0.99)}


def run(config, output):
    validate_config(config)
    output.mkdir(parents=True, exist_ok=True)
    rows, journal, inputs = [], [], []
    for scenario in config['scenarios']:
        for seed in range(config['seed_start'], config['seed_start'] + config['seed_count']):
            case = make_case(config, seed, scenario)
            placements = {strategy: case.initial for strategy in config['strategies']}
            for epoch in range(config['epochs']):
                rates = rates_for_epoch(config, case, scenario, epoch)
                trace, trace_seed, digest = request_trace(config, case, rates, seed, scenario, epoch)
                inputs.append({'scenario': scenario, 'seed': seed, 'epoch': epoch,
                               'trace_seed': trace_seed, 'trace_sha256': digest,
                               'rates': rates, 'write_fractions': case.write_fractions,
                               'sizes_mib': case.sizes, 'speeds': case.speeds,
                               'storage_limit_mib': case.storage_limit,
                               'initial_replicas': case.initial.replicas,
                               'initial_leaders': case.initial.leaders})
                for strategy in config['strategies']:
                    placement, moves = rebalance(placements[strategy], case, rates, strategy, config)
                    placements[strategy] = placement
                    vectors = load_vectors(placement, case, rates, config)
                    rho = [value / speed for value, speed in zip(vectors[1], case.speeds)]
                    row = {'scenario': scenario, 'seed': seed, 'epoch': epoch, 'strategy': strategy,
                           'trace_sha256': digest, 'rho_max': max(rho),
                           'rho_std': statistics.pstdev(rho), 'migration_mib': sum(m['mib'] for m in moves),
                           'actions': len(moves), 'leader_only_actions': sum(m['kind'] == 'leader' for m in moves),
                           'constraint_violations': violations(placement, case, config),
                           **simulate(trace, placement, case, config)}
                    rows.append(row)
                    journal.append({'scenario': scenario, 'seed': seed, 'epoch': epoch,
                                    'strategy': strategy, 'moves': moves,
                                    'replicas': placement.replicas, 'leaders': placement.leaders,
                                    'rho': rho})
            print(f'{scenario}: seed {seed} complete', flush=True)
    with (output / 'epochs.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'inputs.json').write_text(json.dumps(inputs, indent=2))
    (output / 'moves.json').write_text(json.dumps(journal, indent=2))
    (output / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest = {'schema_version': 1, 'run_script_sha256': script_digest,
                'trajectories': len(config['scenarios']) * config['seed_count'] * len(config['strategies']),
                'epoch_rows': len(rows), 'python_requirement': '3.9+',
                'metric_scope': 'FCFS node operations, not end-to-end Raft transactions',
                'files_sha256': {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                                 for name in ['epochs.csv', 'inputs.json', 'moves.json', 'config.json']}}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    parser.add_argument('--out', type=Path, default=Path(__file__).with_name('results'))
    parser.add_argument('--seed-count', type=int)
    arguments = parser.parse_args()
    config = json.loads(arguments.config.read_text())
    if arguments.seed_count is not None:
        config['seed_count'] = arguments.seed_count
    run(config, arguments.out)


if __name__ == '__main__':
    main()
