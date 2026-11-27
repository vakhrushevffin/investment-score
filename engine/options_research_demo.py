"""Deterministic synthetic educational examples, not market observations."""
import pandas as pd

PROFILES = {'A': ([5,7,10,14,18,12,8],[8,12,18,16,12,8,4],[29.4,24.8,23.1,22.6],21.2), 'B': ([3,8,12,16,13,9,5],[4,7,10,14,9,5,3],[36.2,32.1,30.4,29.8],28.4), 'C': ([3,5,8,12,10,6,3],[6,8,10,11,7,4,2],[22.4,19.6,19.1,18.8],17.3)}
def instrument_demo(key):
    c,p,iv,hv=PROFILES[key]
    oi=pd.DataFrame({'Strike label':[f'K{i}' for i in range(1,8)],'Call OI':[x*1000 for x in c],'Put OI':[x*1000 for x in p]}).set_index('Strike label')
    term=pd.DataFrame({'Days':[7,30,60,90],'IV (%)':iv}).set_index('Days')
    calls=int(oi['Call OI'].sum()); puts=int(oi['Put OI'].sum())
    return oi,term,{'Open Interest':calls+puts,'Call OI':calls,'Put OI':puts,'Put/Call OI':puts/calls if calls else None,'IV 30D':iv[1],'HV 20D':hv}
