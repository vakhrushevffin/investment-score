"""Run from repository root with python -m scripts.options_real_pilot."""
import argparse
import csv
import json
from pathlib import Path

from engine.options_real import collect
from engine.options_real_archive import save
from engine.options_real_provider import CboeSample, PILOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--sample', type=Path)
    source.add_argument('--fetch-official-sample', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--confirm-archive-rights', action='store_true')
    parser.add_argument('--retain-raw', action='store_true')
    args = parser.parse_args()
    if (args.archive and not args.confirm_archive_rights) or (args.retain_raw and not args.archive):
        parser.error('Archive requires an explicit rights confirmation; raw retention requires an archive.')
    report, buckets, raw = collect(CboeSample(args.sample))
    with args.report.open('w', encoding='utf-8', newline='') as stream:
        keys = ('availability','rows','normalized','dates','statuses','reasons','field_counts','quote_freshness','oi_freshness')
        writer = csv.writer(stream)
        writer.writerow(('source','ticker','retrieval_utc','file_sha256',*keys))
        for ticker in PILOT:
            summary = report['symbols'].get(ticker, {})
            writer.writerow((report['source'],ticker,report['fetched_at'],report.get('source_sha256'),
                             *(json.dumps(summary.get(k,report['availability'] if k=='availability' else None))
                               if k not in ('availability','rows','normalized')
                               else summary.get(k,report['availability'] if k=='availability' else 0) for k in keys)))
    print(json.dumps(report,indent=2))
    if args.archive:
        print(json.dumps(save(args.archive,report,buckets,raw=raw if args.retain_raw else None)))
    return int(report['availability'] != 'AVAILABLE')


if __name__ == '__main__':
    raise SystemExit(main())
