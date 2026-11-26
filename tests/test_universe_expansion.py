"""Registry and publication regressions. Provider replay uses packaged historical rows."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from engine.price_sync import synchronize
from scripts import update_data

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from bootstrap_prices import stage
from publish_price_update import digest, validate
from safe_price_update import ACTION_FIELDS, load_archive, registry_symbols, run


class UniverseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry_path = ROOT/'data/tickers.csv'
        cls.reg = update_data.registry()
        cls.legacy = pd.read_csv(ROOT/'tests/fixtures/tickers_v2_3.csv')
        cls.baseline = pd.read_csv(ROOT/'data/baseline.csv')
        cls.archive = ROOT/'data/daily_prices.csv.gz'
        cls.history = load_archive(cls.archive)
        cls.last = cls.history.loc[cls.history.ticker.eq('SPY'), 'date'].max()

    def test_registry_and_batch_plans(self):
        self.assertEqual(len(self.reg), 102)
        self.assertEqual(self.reg.cik.nunique(), 102)
        self.assertEqual(self.reg.source_rank.le(101).sum(), 100)
        self.assertEqual(set(self.reg[self.reg.legacy_regression].ticker), set(self.legacy.ticker))
        self.assertEqual(self.reg.set_index('ticker').loc[['MU','HPE','DELL'], 'cik'].tolist(),
                         [723125, 1645590, 1571996])
        self.assertNotIn('SPY', set(self.reg.ticker))
        self.assertEqual(len(registry_symbols(self.registry_path)), 103)
        self.assertEqual(update_data.update_fundamentals(self.reg, 20, dry_run=True),
                         {'selected': 102, 'batches': 6})
        self.assertEqual(update_data.update_prices(self.reg, 40, dry_run=True),
                         {'selected': 103, 'batches': 3})

    def test_regression_all_28_scores_and_price_factors(self):
        old = update_data.snapshot_from_registry(update_data.registry(ROOT/'tests/fixtures/tickers_v2_3.csv'), self.baseline)
        new = update_data.snapshot_from_registry(self.reg, self.baseline)
        actual = synchronize(new, self.history, asof=self.last.date()).set_index('ticker')
        expected = synchronize(old, self.history, asof=self.last.date()).set_index('ticker')
        cols = ['score', 'partial_score', 'quality', 'growth', 'valuation', 'momentum',
                'price', 'sma50', 'sma200', 'rsi14', 'technical_pass']
        self.assertEqual(len(expected), 28)
        pd.testing.assert_frame_equal(actual.loc[expected.index, cols], expected[cols])
        self.assertEqual(len(actual), 102)

    def test_missing_snapshot_is_pending(self):
        snapshot = update_data.snapshot_from_registry(self.reg, self.baseline.iloc[0:0])
        self.assertTrue(snapshot.status.eq('SEC_PENDING').all())
        self.assertTrue(snapshot.revenue_ttm.isna().all())

    def test_fundamental_batch_error_does_not_stop_next_batches(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data').mkdir()
            self.baseline.to_csv(root/'data/baseline.csv', index=False)
            seen = []

            def replay(sub, email, *, refresh_prices):
                self.assertFalse(refresh_prices)
                seen.extend(sub.ticker)
                if len(seen) == 20:
                    raise RuntimeError('Provider offline')
                return sub.assign(status='REVIEW_REQUIRED'), pd.DataFrame()

            with patch.object(update_data, 'ROOT', root), patch.object(update_data, 'refresh', replay):
                result = update_data.update_fundamentals(self.reg, 20, 'test@example.com')
            self.assertEqual(seen, self.reg.ticker.tolist())
            self.assertEqual(result, {'selected': 102, 'updated': 82, 'failed': 20})
            self.assertFalse(pd.read_csv(root/'data/baseline.csv').ticker.duplicated().any())

    def test_price_bootstrap_and_automatic_publication(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive, candidate = root/'archive.csv.gz', root/'candidate.csv.gz'
            reg = root/'tickers.csv'
            self.reg[self.reg.ticker.isin(['AAPL', 'MSFT'])].to_csv(reg, index=False)
            self.history[self.history.ticker.isin(['AAPL', 'SPY'])].to_csv(archive, index=False)
            original = digest(archive)

            def replay(ticker, start):
                rows = self.history[(self.history.ticker == ticker) & (self.history.date >= start)].copy()
                rows[ACTION_FIELDS] = rows[ACTION_FIELDS].fillna(0)
                return rows

            with self.assertRaisesRegex(ValueError, 'failed validation'):
                stage(reg, archive, candidate, lambda ticker, start: replay(ticker, start).iloc[:-1])
            self.assertFalse(candidate.exists())
            self.assertEqual(stage(reg, archive, candidate, replay), 1)
            self.assertEqual(digest(archive), original)
            report = root/'reports'
            prior = root/'prior.csv.gz'
            seeded = load_archive(candidate)
            seeded.loc[seeded.date.lt(self.last)].to_csv(prior, index=False)
            # Replay the real last session as an append to the preceding snapshot.
            self.assertEqual(run(reg, prior, report, 14, 10000, replay), 0)
            verified = validate(prior, reg, report, digest(prior),
                                now=self.last.tz_localize('UTC') + pd.Timedelta(days=1), fetch=replay)
            self.assertEqual(verified['result'], 'PASS')
            self.assertEqual(verified['appended_rows'], 3)
            self.assertTrue((report/'publish_daily_prices.csv.gz').is_file())
            self.assertEqual(digest(archive), original)
