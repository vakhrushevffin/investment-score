"""Transparent technical risk dashboard. Research, not an order execution system."""
import io
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

NAMES=['SMA200','Donchian55','RS100 / SPY','MACD','DMI14','Chandelier ATR','SPY regime']
EXPLAIN=[
 'Скорректированное закрытие ниже SMA200',
 'Закрытие ниже минимума 55 предыдущих закрытий',
 'Отношение цены к SPY ниже собственной SMA100',
 'MACD(12,26) ниже сигнальной линии (9)',
 '−DI(14) выше +DI(14)',
 'Закрытие ниже максимума High(22) минус 3 × ATR(14)',
 'SPY ниже собственной SMA200',
]

@st.cache_data(show_spinner='Расчёт индикаторов…')
def calculate(csv_bytes):
    raw=pd.read_csv(io.BytesIO(csv_bytes),parse_dates=['date'])
    needed={'date','ticker','open','high','low','close','adj_close'}
    if not needed.issubset(raw): raise ValueError(f'Нет столбцов: {sorted(needed-set(raw))}')
    raw=raw.sort_values(['ticker','date']).drop_duplicates(['ticker','date'],keep='last')
    raw=raw[(raw.close>0)&(raw.adj_close>0)]
    if 'SPY' not in raw.ticker.values:raise ValueError('Нужен SPY для относительной силы и режима рынка')
    fac=raw.adj_close/raw.close
    raw['adj_high']=raw.high*fac;raw['adj_low']=raw.low*fac
    c=raw.pivot(index='date',columns='ticker',values='adj_close').sort_index()
    h=raw.pivot(index='date',columns='ticker',values='adj_high').reindex_like(c)
    l=raw.pivot(index='date',columns='ticker',values='adj_low').reindex_like(c)
    spy=c['SPY']; assets=[x for x in c.columns if x not in ('SPY','QQQ','RSP')]
    c,h,l=c[assets],h[assets],l[assets]
    ma200=c.rolling(200,min_periods=200).mean()
    rs=c.div(spy,axis=0); rs_ma=rs.rolling(100,min_periods=100).mean()
    ema12=c.ewm(span=12,adjust=False,min_periods=26).mean()
    ema26=c.ewm(span=26,adjust=False,min_periods=26).mean()
    macd=ema12-ema26; macdsig=macd.ewm(span=9,adjust=False,min_periods=9).mean()
    up=h.diff(); down=-l.diff()
    plus=up.where((up>down)&(up>0),0);minus=down.where((down>up)&(down>0),0)
    tr=pd.concat([(h-l).stack(),(h-c.shift()).abs().stack(),(l-c.shift()).abs().stack()],axis=1).max(axis=1).unstack().reindex_like(c)
    atr=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    pdi=plus.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/atr
    mdi=minus.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/atr
    spyma=spy.rolling(200,min_periods=200).mean()
    flags=[c<ma200,c<c.shift(1).rolling(55,min_periods=55).min(),rs<rs_ma,macd<macdsig,mdi>pdi,c<(h.rolling(22,min_periods=22).max()-3*atr),pd.DataFrame(np.broadcast_to((spy<spyma).to_numpy()[:,None],c.shape),index=c.index,columns=c.columns)]
    valid=(c.notna()&ma200.notna()&rs_ma.notna()&atr.notna()&macdsig.notna()&spyma.notna().to_numpy()[:,None])
    votes=pd.concat({n:f.where(valid).astype('boolean') for n,f in zip(NAMES,flags)},axis=1)
    reds=sum(f.astype(float) for f in flags).where(valid)
    # Three state risk interpretation; 4+ is risk warning, not validated sell instruction.
    state=pd.DataFrame(np.where(reds>=4,'КРАСНЫЙ',np.where(reds>=3,'ЖЁЛТЫЙ','ЗЕЛЁНЫЙ')),index=reds.index,columns=reds.columns).where(valid,'НЕТ ДАННЫХ')
    return raw,c,reds,state,votes,ma200

# Streamlit multipage: main app controls page configuration
st.title('🚦 Технические светофоры')
st.caption('Исследовательский монитор рисков • исторические сигналы • без исполнения сделок')
from pathlib import Path
import gzip
from datetime import date

archive = Path(__file__).resolve().parents[1] / 'data' / 'daily_prices.csv.gz'
with st.expander('Источник котировок и обновление', expanded=False):
 st.caption('По умолчанию используется архив до 20.11.2026. Для актуального анализа загрузите собственный обновлённый CSV. Данные страницы не обновляются автоматически.')
 upload=st.file_uploader('Обновить исторические цены (CSV)',type=['csv'])
if upload is not None:
 csv_bytes=upload.getvalue()
 source='Загруженный CSV'
elif archive.exists():
 with gzip.open(archive,'rb') as f: csv_bytes=f.read()
 source='Встроенный исторический архив'
else:
 st.error('Нет встроенного архива. Загрузите CSV в разделе источника котировок.');st.stop()
try: raw,c,reds,state,votes,ma200=calculate(csv_bytes)
except Exception as e:
 st.error(f'Не удалось обработать данные: {e}');st.stop()
from scripts.update_data import registry
active=set(registry().query('enabled and technical_enabled').ticker)
selected=[t for t in c.columns if t in active]
if not selected: st.error('Нет активных акций с котировками');st.stop()
c=c[selected];reds=reds[selected];state=state[selected];ma200=ma200[selected]
last=c.index.max(); st.caption(f'Источник: {source} · Последняя дата: {last:%d.%m.%Y} · {len(c.columns)} акций · исторические данные, не онлайн-сигналы')
if (pd.Timestamp.today().normalize()-last).days>7: st.warning('Котировки старше 7 календарных дней. Сигналы нельзя использовать как актуальные.')
latest=state.loc[last];lastred=reds.loc[last]
watch=st.multiselect('Акции для наблюдения',list(c.columns),default=list(c.columns))
if not watch:st.stop()
summary=pd.DataFrame({'Тикер':watch,'Режим':[latest[t] for t in watch],'Красных из 7':[int(lastred[t]) if pd.notna(lastred[t]) else None for t in watch], 'Закрытие':[float(c.loc[last,t]) if pd.notna(c.loc[last,t]) else None for t in watch], 'Дата цены':[raw.loc[raw.ticker.eq(t),'date'].max().date().isoformat() if raw.ticker.eq(t).any() else None for t in watch]})
col1,col2,col3=st.columns(3)
col1.metric('Красных акций',int((summary['Режим']=='КРАСНЫЙ').sum()))
col2.metric('Жёлтых акций',int((summary['Режим']=='ЖЁЛТЫЙ').sum()))
col3.metric('Зелёных акций',int((summary['Режим']=='ЗЕЛЁНЫЙ').sum()))
st.subheader('Сводка наблюдения')
st.dataframe(summary,hide_index=True,use_container_width=True)
st.download_button('Экспорт сводки CSV',summary.to_csv(index=False).encode('utf-8-sig'),file_name='technical_risk_summary.csv',mime='text/csv')
st.subheader('Карточка акции')
ticker=st.selectbox('Тикер',watch)
day=st.date_input('Дата анализа',value=last.date(),min_value=c.index.min().date(),max_value=last.date())
idx=c.index[c.index<=pd.Timestamp(day)]
if not len(idx):st.stop()
d=idx[-1]
col1,col2=st.columns([1,2])
with col1:
 st.metric('Состояние',state.loc[d,ticker]);st.metric('Красных',f'{int(reds.loc[d,ticker])}/7' if pd.notna(reds.loc[d,ticker]) else 'н/д')
 st.caption('4+ красных — сигнал риска для анализа, не автоматическая рекомендация продать.')
with col2:
 rows=[]
 for name,explain in zip(NAMES,EXPLAIN):
  v=votes.loc[d,(name,ticker)]
  rows.append({'Индикатор':name,'Светофор':'🔴 Красный' if pd.notna(v) and bool(v) else ('🟢 Зелёный' if pd.notna(v) else '⚪ Недостаточно данных'),'Условие красного':explain})
 st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
prices=c[ticker].loc[:d].tail(450);sma=ma200[ticker].reindex(prices.index)
fig=go.Figure();fig.add_trace(go.Scatter(x=prices.index,y=prices,name='Adj close'));fig.add_trace(go.Scatter(x=sma.index,y=sma,name='SMA200'));fig.update_layout(height=360,margin=dict(l=10,r=10,t=25,b=10),title=f'{ticker}: цена и SMA200');st.plotly_chart(fig,use_container_width=True)
st.subheader('История предупреждений')
hist=pd.DataFrame({'Дата':state.index,'Состояние':state[ticker].values,'Красных':reds[ticker].values})
hist['Предыдущее']=hist['Состояние'].shift(1)
changes=hist[(hist['Состояние']!=hist['Предыдущее'])&(hist['Состояние']!='НЕТ ДАННЫХ')].sort_values('Дата',ascending=False)
st.dataframe(changes.head(40).drop(columns='Предыдущее'),hide_index=True,use_container_width=True)
st.download_button('Скачать историю сигналов',hist.drop(columns='Предыдущее').to_csv(index=False).encode('utf-8-sig'),file_name=f'{ticker}_signal_history.csv',mime='text/csv')
st.subheader('Риск портфеля (не прогноз просадки)')
st.caption(f'Светофоры портфеля на {d:%d.%m.%Y}. Веса вводятся вручную и не нормализуются. Для исторической даты указывайте исторические веса.')
weights={}
for t in watch:
 weights[t]=st.number_input(f'{t}: вес в портфеле, %',min_value=0.,max_value=100.,value=round(100/len(watch),2),step=1.,key=f'w_{t}')
wt=sum(weights.values())
if wt>100.01:st.warning(f'Сумма весов {wt:.1f}% превышает 100%')
redweight=sum(w for t,w in weights.items() if state.loc[d,t]=='КРАСНЫЙ')
yellowweight=sum(w for t,w in weights.items() if state.loc[d,t]=='ЖЁЛТЫЙ')
st.metric('Доля капитала под красными сигналами',f'{redweight:.1f}%')
st.metric('Доля капитала под жёлтыми сигналами',f'{yellowweight:.1f}%')
st.caption('Сигналы коррелируют: семь голосов не равны семи независимым источникам. Историческая эффективность сигналов требует отдельной валидации; сегодняшняя вселенная акций создаёт survivorship bias.')
