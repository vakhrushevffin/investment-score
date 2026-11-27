import os
from pathlib import Path
import sys
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from engine.options_market import demo, load_history, common_snapshot, SYMBOLS
from engine.options_research_demo import instrument_demo, PROFILES
from engine.options_quality_demo import checks, archive_checks
from engine.options_real import collect
from engine.options_real_provider import CboeSample, PILOT

st.set_page_config(page_title='Options Market Monitor',layout='wide')

sample_path = os.getenv('OPTIONS_REAL_SAMPLE_PATH')
if sample_path and os.getenv('OPTIONS_REAL_DISPLAY_AUTHORIZED') == '1':
    @st.cache_data(ttl=900, max_entries=2, show_spinner=False)
    def load_sample(path, file_mtime, file_size):
        report, rows, _ = collect(CboeSample(Path(path)))
        return report, rows

    try:
        metadata = Path(sample_path).stat()
        real_report, real_rows = load_sample(sample_path, metadata.st_mtime_ns, metadata.st_size)
    except OSError:
        real_report, real_rows, _ = collect(CboeSample(Path(sample_path)))
else:
    real_report, real_rows = {'availability': 'UNAVAILABLE', 'reason': 'No authorized local sample configured'}, {}

st.title('Options Market Monitor')
st.caption('v2.5.4 | Historical data pilot | Educational statistics')
market,instrument,quality,method=st.tabs(['Market Overview','Instrument Research','Data Quality','Methodology'])
with market:
    st.subheader('Real option observation coverage')
    if real_report.get('symbols'):
        st.caption(real_report['source'] + ' | Fixed historical sample, market-wide coverage unverified')
        st.dataframe(pd.DataFrame([
            {'Instrument': ticker, 'Observation dates': ', '.join(item['dates']),
             'Normalized rows': item['normalized'], 'Availability': item['availability']}
            for ticker, item in real_report['symbols'].items()
        ]), hide_index=True)
    else:
        st.info(real_report.get('reason', 'Historical option sample unavailable.'))
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
    st.subheader('Historical option observations')
    ticker = st.selectbox('Pilot instrument', PILOT, key='options_real_instrument')
    observations = real_rows.get(ticker, [])
    if observations:
        st.caption(real_report['source'] + ' | Demo coverage unknown | 15:45 ET snapshot and separate EOD fields')
        st.dataframe(pd.DataFrame(observations), hide_index=True)
    else:
        st.info('No authorized historical observations for this instrument.')
    st.info('Chain-wide ratios and concentrations are withheld because coverage and the OI effective date are unverified.')
    st.divider()
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
    st.subheader('Gamma Research'); st.info('Model assumptions do not identify observed participant positions. No exposure estimates or predictions are provided.')
with quality:
    st.subheader('Data Quality Monitor')
    st.warning('SYNTHETIC — controlled quality checks; not validation of real market data')
    results=pd.DataFrame(checks()+archive_checks())
    st.metric('Controlled checks passed',f"{int(results.Passed.sum())}/{len(results)}")
    st.dataframe(results,hide_index=True)
    st.download_button('Download quality check log',results.to_csv(index=False),file_name='synthetic_quality_checks.csv')
    st.caption('Expected rejection counts as a passed test. Rounding compatibility is separate from materiality.')
    st.subheader('Real Data Quality')
    st.write('Source', real_report.get('source', 'Cboe DataShop sample 217'))
    st.write('Availability', real_report['availability'])
    st.write('Last successful retrieval or local import (UTC)',
             real_report.get('last_successful_retrieval') or '—')
    if real_report.get('reason'):
        st.info(real_report['reason'])
    for symbol, summary in real_report.get('symbols', {}).items():
        with st.expander(symbol + ' | ' + summary['availability'], expanded=symbol == 'SPY'):
            st.write('Received / normalized', f"{summary['rows']} / {summary['normalized']}")
            st.write('Quality status counts', summary['statuses'])
            st.write('Quote freshness', summary['quote_freshness'])
            st.write('Open interest freshness', summary['oi_freshness'])
            st.write('Exceptions', summary['reasons'])
            if summary['rows']:
                st.dataframe(pd.DataFrame(summary['field_counts'].items(),
                                         columns=['Field', 'Present']).assign(Received=summary['rows']),
                             hide_index=True)
with method:
    st.subheader('Research Methodology')
    for name,desc in [('Open Interest','Number of outstanding contracts.'),('Put/Call OI','Put open interest divided by call open interest.'),('Implied Volatility','Volatility inferred from an option pricing model.'),('Historical Volatility','Dispersion of historical returns; demo values are illustrative inputs.'),('Gamma','Second derivative of option value with respect to underlying price.'),('Model uncertainty','Open interest does not identify signed participant positions. Predictive properties have not been established.')]:
        st.markdown(f'**{name}**'); st.write(desc)
    st.caption('Cboe sample 217 is a fixed historical extract. The 15:45 ET label refers to the nominal snapshot. Exact quote time, effective OI date, revision order and contract multiplier are unknown. Zero IV of unknown origin is treated as missing in normalized observations.')
    st.caption('Research basis: OMR-001 through OMR-006.1.')
