import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import pandas as pd
from reconcile_prices import reconcile, normalize
from safe_price_update import load_archive
class Tests(unittest.TestCase):
 def setUp(self):
  self.a=pd.DataFrame({'date':pd.date_range('2026-01-01',periods=3),'ticker':'X','open':100.,'high':101.,'low':99.,'close':100.,'adj_close':100.,'volume':1000.,'dividends':0.,'splits':0.,'capital_gains':0.})
 def live(self,split=0,div=0,already=False):
  a=self.a.copy(); f=a.iloc[-1:].copy(); f.date+=pd.Timedelta(days=1); f.splits=split; f.dividends=div
  if split and not already:
   a[['open','high','low','close','adj_close']]/=split
   f[['open','high','low','close','adj_close']]/=split
  a.adj_close*=1-div/a.close.iloc[-1]
  return pd.concat([a,f],ignore_index=True)
 def test_dividend(self): self.assertAlmostEqual(reconcile(self.a,self.live(div=30)).adj_close.iloc[0],70)
 def test_forward(self): self.assertAlmostEqual(reconcile(self.a,self.live(split=10)).close.iloc[0],10)
 def test_reverse(self): self.assertAlmostEqual(reconcile(self.a,self.live(split=.1)).close.iloc[0],1000)
 def test_already(self): self.assertEqual(reconcile(self.a,self.live(split=10,already=True)).close.iloc[0],100)
 def test_repeat(self):
  live=self.live(div=5); a=reconcile(self.a,live); pd.testing.assert_frame_equal(a,reconcile(a,live))
 def test_unexplained(self):
  live=self.live(); live.loc[0,'close']=99.5
  with self.assertRaises(ValueError): reconcile(self.a,live)
 def test_missing(self):
  with self.assertRaises(ValueError): reconcile(self.a,self.live().iloc[1:])
 def test_alias(self): self.assertIn('splits',normalize(self.live().rename(columns={'splits':'stock_splits'}).drop(columns='capital_gains')))
 def test_missing_split(self):
  with self.assertRaises(ValueError): normalize(self.live().drop(columns='splits'))
 def test_archive(self):
  a=load_archive(Path(__file__).resolve().parents[1]/'data/daily_prices.csv.gz'); self.assertGreaterEqual(len(a),92719)
if __name__=='__main__': unittest.main()
