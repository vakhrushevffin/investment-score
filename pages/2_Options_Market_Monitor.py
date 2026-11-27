from pathlib import Path
import sys
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from engine.options_market import demo, load_history, common_snapshot, SYMBOLS
from engine.options_research_demo import instrument_demo, PROFILES

st.set_page_config(page_title='Options Market Monitor',layout='wide')
st.title('Options Market Monitor')
st.caption('v2.5.2 | Research interface prototype | Educational statistics')
market,instrument,quality,method=st.tabs(['Market Overview','Instrument Research','Data Quality','Methodology'])
with market:
    mode=st.radio('Data mode',['DEMO','HISTORICAL'],horizontal=True)
    d=None
    if mode=='DEMO':
        st.warning('SYNTHETIC — not market observations')
        d=demo()
    else:
        try: d=load_history(ROOT/'data'/'options')
        except (ValueError,OSError) as e: st.info(f'Historical data unavailable: {e}')
        if d is not None: st.caption('Local historical archive; permissions declared by archive owner.')
    if d is not None:
        selected=[]
        for symbol in sorted(d.series.unique()):
            part=d[d.series.eq(symbol)]; sources=sorted(part.source_id.unique())
            source=st.selectbox(f'Source: {symbol}',sources,key=f'options_source_{symbol}') if len(sources)>1 else sources[0]
            selected.append(part[part.source_id.eq(source)])
        d=pd.concat(selected,ignore_index=True)
        for col,symbol in zip(st.columns(4),SYMBOLS):
            part=d[d.series.eq(symbol)].sort_values('date')
            if part.empty: col.metric(symbol,'—'); continue
            last=part.iloc[-1]; col.metric(symbol,f'{last.value:.2f}'); col.caption(f'{last.date:%Y-%m-%d} | {last.source_id}')
        st.subheader('Volatility Term Structure')
        snapshot=common_snapshot(d)
        if snapshot is None: st.info('No comparable common-date snapshot.')
        else:
            st.caption(f'Common date: {snapshot.name:%Y-%m-%d}')
            st.line_chart(pd.DataFrame({'Days':[9,30,90],'Volatility points':snapshot.reindex(SYMBOLS[:3]).values}).set_index('Days'))
        period=st.selectbox('History period',['1 month','3 months','6 months','1 year','5 years','All'],index=3)
        months={'1 month':1,'3 months':3,'6 months':6,'1 year':12,'5 years':60}
        view=d if period not in months else d[d.date>=d.date.max()-pd.DateOffset(months=months[period])]
        for title,mask in [('Volatility history',view.series.isin(SYMBOLS)),('Put/Call history',~view.series.isin(SYMBOLS))]:
            st.subheader(title); part=view[mask]
            if part.empty: st.info('No observations'); continue
            st.line_chart(part.pivot(index='date',columns='series',values='value'))
            with st.expander('Observations'): st.dataframe(part,hide_index=True)
            st.download_button('Download CSV — '+title,part.to_csv(index=False),file_name=f'{mode.lower()}_{title.replace(" ","_")}.csv')
with instrument:
    st.warning('SYNTHETIC — abstract instruments only; not market quotes')
    key=st.selectbox('Instrument',list(PROFILES),format_func=lambda x:f'Instrument {x}')
    oi,term,metrics=instrument_demo(key)
    for i,(name,value) in enumerate(metrics.items()):
        if i%3==0: cols=st.columns(3)
        display=f'{value:,.0f}' if name in ['Open Interest','Call OI','Put OI'] else f'{value:.2f}' if name=='Put/Call OI' else f'{value:.1f}%'
        cols[i%3].metric(name,display)
    st.subheader('Open Interest Distribution'); st.bar_chart(oi)
    st.download_button('Download synthetic OI',oi.to_csv(),file_name=f'synthetic_{key}_oi.csv')
    st.subheader('Volatility Term Structure'); st.line_chart(term)
    st.subheader('Gamma Research'); st.info('Real chain data: UNAVAILABLE. Model assumptions do not identify observed participant positions. No exposure estimates or predictions are provided.')
with quality:
    st.subheader('Data Quality Monitor')
    st.table(pd.DataFrame([('Synthetic instrument examples','VALID (fixture only)'),('Real option chains','UNAVAILABLE'),('Usage rights','UNVERIFIED'),('Daily collection','UNVERIFIED'),('Production archive','UNVERIFIED')],columns=['Scope','Status']))
    st.caption('OMR-004 / OMR-006.1 principles; production validators and versioning are not integrated in this prototype.')
with method:
    st.subheader('Research Methodology')
    for name,desc in [('Open Interest','Number of outstanding contracts.'),('Put/Call OI','Put open interest divided by call open interest.'),('Implied Volatility','Volatility inferred from an option pricing model.'),('Historical Volatility','Dispersion of historical returns; demo values are illustrative inputs.'),('Gamma','Second derivative of option value with respect to underlying price.'),('Model uncertainty','Open interest does not identify signed participant positions. Predictive properties have not been established.')]:
        st.markdown(f'**{name}**'); st.write(desc)
    st.caption('Research basis: OMR-001 through OMR-006.1.')
