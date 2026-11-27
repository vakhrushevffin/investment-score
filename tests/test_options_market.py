import unittest
import tempfile
from pathlib import Path
import pandas as pd
from engine.options_market import demo,validate,common_snapshot,load_history
class TestMarket(unittest.TestCase):
    def test_demo(self):
        self.assertEqual(len(demo()),2080)
        pd.testing.assert_frame_equal(demo(),demo())
    def test_duplicate(self):
        d=demo(); self.assertRaises(ValueError,validate,pd.concat([d,d.iloc[:1]]))
    def test_negative(self):
        d=demo(); d.loc[d.index[0],'value']=-1; self.assertRaises(ValueError,validate,d)
    def test_missing(self):
        d=demo(); d.loc[d.index[0],'value']=float('nan'); self.assertRaises(ValueError,validate,d)
    def test_same_date(self):
        d=demo(); s=common_snapshot(d); self.assertEqual(s.name,d.date.max())
        d=d[~((d.date==s.name)&(d.series=='VIX'))]; self.assertLess(common_snapshot(d).name,s.name)
    def test_session(self):
        d=demo(); d['session']=d.series; self.assertIsNone(common_snapshot(d))
    def test_empty(self):
        self.assertRaises(ValueError,load_history,Path('data/options'))
    def test_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); d=demo().iloc[:1].copy(); d['source_id']='TEST'; d.to_csv(p/'market_history.csv',index=False)
            r=pd.DataFrame([dict(source_id='TEST',series=d.series.iloc[0],source_url='https://example.org',scope='test',methodology='test',session='test',display_permission='PENDING',permission_reference='test')]); r.to_csv(p/'source_registry.csv',index=False)
            self.assertRaises(ValueError,load_history,p)
            r['display_permission']='APPROVED'; r.to_csv(p/'source_registry.csv',index=False); self.assertEqual(len(load_history(p)),1)
if __name__=='__main__': unittest.main()
