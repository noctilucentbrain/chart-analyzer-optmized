"""Offline CPU benchmark; no Yahoo calls or database writes.

Run: python benchmarks/benchmark.py --baseline /path/to/original/chart-analyzer
"""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import time

import pandas as pd
from chart_analyzer.indicators import compute_indicators, INDICATORS


def measure(fn, repeats):
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--rows', type=int, default=756)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('baseline_indicators', args.baseline / 'src/chart_analyzer/indicators.py')
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    n = args.rows
    candles = pd.DataFrame({
        'ticker': ['TEST'] * n, 'timeframe': ['daily'] * n,
        'timestamp': pd.date_range('2020-01-01', periods=n, tz='UTC'),
        'open': [100 + i % 11 for i in range(n)], 'high': [115 + i % 11 for i in range(n)],
        'low': [95 + i % 11 for i in range(n)], 'close': [100 + i % 13 for i in range(n)],
        'volume': [1000 + i % 23 * 37 for i in range(n)],
    })
    indicators = list(INDICATORS)
    old = lambda: baseline.compute_indicators(candles, indicators, {})
    new = lambda: compute_indicators(candles, indicators, {}, output_index=candles.tail(1).index)
    expected = old()
    pd.testing.assert_frame_equal(compute_indicators(candles, indicators, {}), expected)
    pd.testing.assert_frame_equal(new(), expected.loc[expected.timestamp == candles.timestamp.iloc[-1]].reset_index(drop=True))
    before = measure(old, args.repeats)
    after = measure(new, args.repeats)
    print(json.dumps({'history_rows':n, 'indicators':len(indicators), 'requested_rows':1,
        'repeats': args.repeats, 'baseline_seconds':before, 'optimized_seconds':after,
        'cpu_speedup':before / after, 'full_and_selected_output_parity':True,
        'scope':'Synthetic indicator CPU only; excludes network and MongoDB latency.'}, indent=2))


if __name__ == '__main__':
    main()
