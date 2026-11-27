from pathlib import Path
import sys
import streamlit as st
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from engine.options_market import demo, load_history, common_snapshot, SYMBOLS

st.set_page_config(page_title='Options Market Monitor',layout='wide')
st.title('Options Market Monitor — Market Overview')
st.caption('v2.5.1 | Образовательная статистика, не торговые сигналы')
mode = st.radio('Режим данных',['DEMO','HISTORICAL'],horizontal=True)
if mode == 'DEMO':
    st.warning('СИНТЕТИЧЕСКИЕ ДАННЫЕ — не реальные рыночные наблюдения')
    d = demo()
else:
    try: d = load_history(ROOT/'data'/'options')
    except (ValueError,OSError) as e:
        st.info(f'Исторические данные не подключены: {e}')
        st.stop()
    st.caption('Локальные исторические данные; не онлайн-котировки. Разрешения декларируются владельцем данных.')
# Explicitly select one archive per series, never splice archives.
selected=[]
for symbol in sorted(d.series.unique()):
    part=d[d.series.eq(symbol)]
    sources=sorted(part.source_id.unique())
    source=st.selectbox(f'Источник {symbol}',sources,key=f'options_source_{symbol}') if len(sources)>1 else sources[0]
    selected.append(part[part.source_id.eq(source)])
import pandas as pd
d=pd.concat(selected,ignore_index=True)
cols=st.columns(4)
for col,symbol in zip(cols,SYMBOLS):
    part=d[d.series.eq(symbol)].sort_values('date')
    if part.empty: col.metric(symbol,'—'); continue
    last=part.iloc[-1]
    col.metric(symbol,f'{last.value:.2f}')
    col.caption(f'{last.date:%Y-%m-%d} | {last.source_id}')
st.subheader('Структура волатильности по срокам')
snapshot=common_snapshot(d)
if snapshot is None: st.info('Нет сопоставимого среза по общей дате и сессии.')
else:
    st.caption(f'Общая дата: {snapshot.name:%Y-%m-%d}; пункты волатильности')
    st.bar_chart(snapshot.rename('value'))
    if snapshot['VIX3M']>0: st.metric('VIX / VIX3M',f"{snapshot['VIX']/snapshot['VIX3M']:.3f}")
st.subheader('История индексов')
period=st.selectbox('Период',['1 месяц','3 месяца','6 месяцев','1 год','5 лет','Вся история'],index=3)
months={'1 месяц':1,'3 месяца':3,'6 месяцев':6,'1 год':12,'5 лет':60}
view=d if period not in months else d[d.date>=d.date.max()-pd.DateOffset(months=months[period])]
for title, mask in [('Индексы волатильности',view.series.isin(SYMBOLS)),('Put/Call — отдельные архивы',~view.series.isin(SYMBOLS))]:
    st.subheader(title)
    part=view[mask]
    if part.empty: st.info('Нет данных за выбранный период'); continue
    st.line_chart(part.pivot(index='date',columns='series',values='value'))
    st.dataframe(part.sort_values('date',ascending=False),hide_index=True,use_container_width=True)
    st.download_button('CSV — '+title,part.to_csv(index=False).encode('utf-8-sig'),file_name=f'{mode.lower()}_{"volatility" if title.startswith("Индексы") else "put_call"}.csv')
st.info('Опционные цепочки, IV отдельных акций и открытый интерес не подключены. Автоматическое обновление отсутствует.')
