import unittest
import pandas as pd
import numpy as np
from engine.price_sync import synchronize
class PriceSyncTests(unittest.TestCase):
    def setUp(self):
        self.d=pd.bdate_range(end='2026-11-25',periods=300)
        self.h=pd.concat([pd.DataFrame({'ticker':t,'date':self.d,'close':np.arange(300)+100.,'adj_close':np.arange(300)+100.,'splits':0.}) for t in ['A','SPY']],ignore_index=True)
        self.b=pd.DataFrame([{'ticker':'A','shares_outstanding':100,'shares_asof':'2026-10-01','price':1,'quote_date':'2026-11-20'}])
    def run_sync(self):return synchronize(self.b,self.h,'2026-11-26').iloc[0]
    def test_price(self):self.assertEqual(self.run_sync().price,399)
    def test_momentum(self):self.assertAlmostEqual(self.run_sync().mom12_1,399/147-1)
    def test_market_cap(self):self.assertEqual(self.run_sync().market_cap,39900)
    def test_missing(self):
        self.h=self.h[~((self.h.ticker=='A')&(self.h.date==self.d[-1]))];self.assertTrue(pd.isna(self.run_sync().price))
    def test_duplicate(self):
        self.h=pd.concat([self.h,self.h.iloc[:1]]);self.assertRaises(ValueError,self.run_sync)
    def test_split(self):
        self.h.loc[(self.h.ticker=='A')&(self.h.date==self.d[-1]),'splits']=2;self.assertTrue(pd.isna(self.run_sync().market_cap))
    def test_stale(self):self.assertRaises(ValueError,synchronize,self.b,self.h,'2026-12-10')
    def test_no_mutation(self):
        old=self.b.copy();self.run_sync();pd.testing.assert_frame_equal(old,self.b)
    def test_adjusted_momentum(self):
        self.h.loc[(self.h.ticker=='A')&(self.h.date==self.d[-253]),'adj_close']=100;self.assertAlmostEqual(self.run_sync().mom12_1,399/100-1)
if __name__=='__main__':unittest.main()
