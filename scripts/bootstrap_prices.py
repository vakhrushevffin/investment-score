"""Stage Yahoo history for new registry symbols without changing existing CSV cells."""
import argparse
import gzip
import tempfile
from pathlib import Path

import pandas as pd

from safe_price_update import (
    ROOT, ACTION_FIELDS, archive_coverage, check_prices, fetch_one, load_archive,
    registry_symbols,
)


def stage(registry, archive, output, fetch=fetch_one, *, limit=None, symbols=None):
    if archive.resolve() == output.resolve():
        raise ValueError('Use a separate output path for the staged archive')
    old = load_archive(archive)
    covered, pending = archive_coverage(registry_symbols(registry), old)
    if symbols:
        unexpected = set(symbols) - set(pending)
        if unexpected:
            raise ValueError(f'Symbols already present or disabled: {sorted(unexpected)}')
        pending = [ticker for ticker in pending if ticker in symbols]
    pending = pending[:limit]
    if not pending:
        return 0
    if not set(ACTION_FIELDS).issubset(old.columns):
        raise ValueError('Baseline lacks corporate-action columns')
    dates = old[old.ticker.eq('SPY')].date.sort_values()
    cutoff = dates.iloc[-1]
    if len(dates) < 253 or not old[old.ticker.isin(covered)].groupby('ticker').date.max().eq(cutoff).all():
        raise ValueError('Current stock and SPY histories must share at least the latest session')
    recent = set(dates.iloc[-253:])
    start = dates.iloc[0].date().isoformat()
    added, errors = [], []
    for ticker in pending:
        try:
            history = fetch(ticker, start)
            history = history.loc[history.date.le(cutoff)]
            check_prices(history, ticker, [ticker])
            if not recent.issubset(set(history.date)):
                raise ValueError('Missing benchmark sessions within the latest 253 sessions')
            if not set(history.date).issubset(set(dates)):
                raise ValueError('Unexpected sessions outside SPY history')
            added.append(history[old.columns])
            print(f'{ticker}: {len(history)} sessions through {cutoff.date()}', flush=True)
        except Exception as exc:
            errors.append(f'{ticker}: {exc}')
            print(errors[-1], flush=True)
    if errors:
        raise ValueError(f'{len(errors)} missing histories failed validation')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.gz', delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with gzip.open(archive, 'rt', newline='') as original, gzip.open(temporary, 'wt', newline='') as target:
            last_line = '\n'
            for last_line in original:
                target.write(last_line)
            if not last_line.endswith('\n'):
                target.write('\n')
            pd.concat(added, ignore_index=True).to_csv(target, header=False, index=False, date_format='%Y-%m-%d')
        candidate = load_archive(temporary)
        check_prices(candidate, 'Seeded archive', set(old.ticker) | set(pending))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return len(pending)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registry', type=Path, default=ROOT/'data/tickers.csv')
    parser.add_argument('--archive', type=Path, default=ROOT/'data/daily_prices.csv.gz')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, help='Maximum new symbols to stage in this invocation')
    parser.add_argument('--symbols', nargs='+', help='Stage only the specified missing tickers')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    count = stage(args.registry, args.archive, args.output, limit=args.limit, symbols=args.symbols)
    print(f'New symbols staged: {count}')


if __name__ == '__main__':
    main()
