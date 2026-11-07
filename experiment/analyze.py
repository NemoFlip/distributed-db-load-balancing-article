"""Aggregate paired trajectories; bootstrap uses seeds as the independent unit."""

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import random
import statistics


def bootstrap_mean(values, seed, repeats=10000):
    rng = random.Random(seed)
    samples = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(repeats))
    return statistics.mean(values), samples[int(0.025 * repeats)], samples[int(0.975 * repeats)]


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def analyze(directory):
    groups = defaultdict(list)
    with (directory / 'epochs.csv').open() as stream:
        for row in csv.DictReader(stream):
            groups[(row['scenario'], row['strategy'], int(row['seed']))].append(row)
    trajectories = []
    for (scenario, strategy, seed), rows in sorted(groups.items()):
        operations = sum(int(row['operations']) for row in rows)
        trajectories.append({
            'scenario': scenario, 'strategy': strategy, 'seed': seed,
            'mean_ms': sum(float(row['mean_ms']) * int(row['operations']) for row in rows) / operations,
            'p95_window_mean_ms': statistics.mean(float(row['p95_ms']) for row in rows),
            'p99_window_mean_ms': statistics.mean(float(row['p99_ms']) for row in rows),
            'rho_std': statistics.mean(float(row['rho_std']) for row in rows),
            'rho_max': max(float(row['rho_max']) for row in rows),
            'migration_mib': sum(float(row['migration_mib']) for row in rows),
            'actions': sum(int(row['actions']) for row in rows),
            'violations': sum(int(row['constraint_violations']) for row in rows),
            'overloaded_windows': sum(float(row['rho_max']) >= 1 for row in rows)})
    metrics = ['mean_ms', 'p95_window_mean_ms', 'p99_window_mean_ms',
               'rho_std', 'rho_max', 'migration_mib', 'actions']
    by_strategy = defaultdict(list)
    for row in trajectories:
        by_strategy[(row['scenario'], row['strategy'])].append(row)
    summary = []
    for group_index, ((scenario, strategy), rows) in enumerate(sorted(by_strategy.items())):
        item = {'scenario': scenario, 'strategy': strategy, 'seeds': len(rows)}
        for index, metric in enumerate(metrics):
            mean, low, high = bootstrap_mean([row[metric] for row in rows], 90000 + group_index * 10 + index)
            item[metric], item[metric + '_low'], item[metric + '_high'] = mean, low, high
        item['violations'] = sum(row['violations'] for row in rows)
        item['overloaded_windows'] = sum(row['overloaded_windows'] for row in rows)
        summary.append(item)
    lookup = {(row['scenario'], row['strategy'], row['seed']): row for row in trajectories}
    paired = []
    for scenario_index, scenario in enumerate(sorted({row['scenario'] for row in trajectories})):
        seeds = sorted(row['seed'] for row in trajectories if row['scenario'] == scenario and row['strategy'] == 'cost')
        for baseline_index, baseline in enumerate(['count', 'qps', 'work']):
            for metric_index, metric in enumerate(['mean_ms', 'rho_std', 'migration_mib']):
                ours = [lookup[(scenario, 'cost', seed)][metric] for seed in seeds]
                theirs = [lookup[(scenario, baseline, seed)][metric] for seed in seeds]
                differences = [left - right for left, right in zip(ours, theirs)]
                mean, low, high = bootstrap_mean(differences,
                                                 80000 + 100 * scenario_index + 10 * baseline_index + metric_index)
                denominator = statistics.mean(theirs)
                paired.append({'scenario': scenario, 'baseline': baseline, 'metric': metric,
                               'cost_minus_baseline': mean, 'low': low, 'high': high,
                               'relative_change_percent': (100 * mean / denominator if denominator else '')})
    write_csv(directory / 'trajectories.csv', trajectories)
    write_csv(directory / 'summary.csv', summary)
    write_csv(directory / 'paired.csv', paired)
    (directory / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({'trajectories': len(trajectories),
                      'constraint_violations': sum(row['violations'] for row in trajectories),
                      'summary': [{key: row[key] for key in ['scenario', 'strategy', 'mean_ms', 'rho_std', 'migration_mib']}
                                  for row in summary]}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, nargs='?', default=Path(__file__).with_name('results'))
    analyze(parser.parse_args().directory)
