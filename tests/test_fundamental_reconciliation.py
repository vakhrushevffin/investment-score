import unittest,datetime as dt
from engine.fundamental_reconciliation import resolve
class TestReconciliation(unittest.TestCase):
 def test_comparative_and_ytd(self):
  rows=[]
  for year in (2025,2026):
   for start,end,val in [('01-01','03-31',10),('01-01','06-30',30),('01-01','09-30',60),('01-01','12-31',100)]:
    rows.append(dict(start=f'{year}-{start}',end=f'{year}-{end}',val=val*(year-2024),filed='2027-02-01',accn='a',form='10-K'))
  data={'facts':{'us-gaap':{'Revenues':{'units':{'USD':rows}}}}}
  r,g=resolve(data,'revenue','GOOGL',dt.date(2026,12,31),dt.date(2027,2,2));self.assertEqual(r['value'],200);self.assertEqual(g,1)
  self.assertIsNone(resolve(data,'revenue','GOOGL',dt.date(2026,12,31),dt.date(2026,12,31))[0])
 def test_missing_operating_income(self):
  self.assertEqual(resolve({},'op_income','CVX',dt.date(2026,9,30),dt.date(2026,11,26)),(None,None))
if __name__=='__main__':unittest.main()
