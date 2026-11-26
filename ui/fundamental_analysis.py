import datetime as dt
import io
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from engine.refresh import refresh

from pathlib import Path
from engine.price_sync import synchronize
ROOT=Path(__file__).resolve().parents[1]
@st.cache_data
def read_history(blob):
    return pd.read_csv(io.BytesIO(blob),compression='gzip')
try:
    history=read_history((ROOT/'data/daily_prices.csv.gz').read_bytes())
except Exception as exc:
    st.error(f'Архив котировок недоступен: {exc}');st.stop()
@st.cache_data
def initial(blob, registry_blob):
    from scripts.update_data import registry, snapshot_from_registry
    return snapshot_from_registry(registry(io.BytesIO(registry_blob)), pd.read_csv(io.BytesIO(blob)))
if 'snapshot' not in st.session_state:
    st.session_state.snapshot = initial(
        (ROOT/'data/baseline.csv').read_bytes(), (ROOT/'data/tickers.csv').read_bytes()
    )
if 'logs' not in st.session_state:st.session_state.logs=pd.DataFrame()
if 'approvals' not in st.session_state:st.session_state.approvals={}
if 'refresh_at' not in st.session_state:st.session_state.refresh_at='Не выполнялось'

st.title('Investment Score')
st.caption('Масштабируемый реестр до 1000 акций США · Исследовательский рейтинг и поддержка решений · Без исполнения сделок')
with st.sidebar:
    st.header('Управление')
    st.caption('Котировки: опубликованный исторический архив. SEC: отдельный снимок.')
    email=st.text_input('Контактный email для SEC',value='',help='SEC требует идентифицирующий User-Agent. Email используется только при запросах к SEC.')
    st.caption('Ручное обновление: SEC Companyfacts. Цены: опубликованный архив Yahoo Finance.')
    if st.button('↻ Обновить данные SEC',type='primary',use_container_width=True):
        bar=st.progress(0,text='Подготовка')
        try:
            new,log=refresh(st.session_state.snapshot,email,lambda p,m:bar.progress(min(1.,p),text=m),history=history)
            st.session_state.snapshot=new;st.session_state.logs=log
            st.session_state.refresh_at=dt.datetime.now().strftime('%Y-%m-%d %H:%M')
            st.success('Обновление завершено. Проверьте журнал ошибок и статусы.')
        except Exception as e:st.error(f'Обновление не выполнено: {e}')
        finally:bar.empty()
    st.divider()
    capital=st.number_input('Капитал портфеля, USD',min_value=1000.,value=1_000_000.,step=10000.)
    max_stock=st.slider('Лимит на акцию, %',1.,15.,5.,.5)/100
    max_sector=st.slider('Лимит на сектор, %',5.,50.,25.,1.)/100
    risk_budget=st.slider('Риск на сделку, % капитала',.1,2.,.5,.1)/100
    st.caption('Параметры — пример политики риска, не результаты оптимизации.')
    st.divider()
    st.caption('Последнее обновление в сессии: '+st.session_state.refresh_at)
    st.warning('На бесплатном облачном хостинге обновления и решения в памяти могут исчезнуть после перезапуска. Скачивайте CSV.')

try:
    df=synchronize(st.session_state.snapshot,history)
except Exception as exc:
    st.error(f'Синхронизация не выполнена: {exc}');st.stop()
if not df.price_sync_status.eq('OK').all():
    st.warning('Некоторые строки требуют проверки: '+', '.join(df.loc[~df.price_sync_status.eq('OK'),'ticker']))
for col in ('score','partial_score','ranking_score','price','completeness','rsi14','sma50','sma200'):
    df[col]=pd.to_numeric(df[col],errors='coerce')
full=int(df.score.notna().sum());partial=int((df.score.isna() & df.partial_score.notna()).sum())
a,b,c,d=st.columns(4)
a.metric('Компаний', len(df))
b.metric('Полный Score', full)
c.metric('Неполный Score', partial)
latest_quote = pd.to_datetime(df.quote_date, errors='coerce').max()
d.metric('Котировки на', latest_quote.date().isoformat() if pd.notna(latest_quote) else '—')
if df.ranking_score.isna().any():
    st.caption(f'Без рейтингового балла: {df.ranking_score.isna().sum()}')
st.info('Полный Score означает наличие четырёх вычислимых факторов, а НЕ ручную верификацию отчётности. Компании с неполными данными и банки остаются в рейтинге. Неполный Score — средневзвешенное только доступных факторов, НЕ подтверждённый полный Score. Ранги разных степеней полноты несопоставимы по надёжности.')

page=st.tabs(['🏆 Рейтинг','📈 Компания','🧮 Размер позиции','🗂️ История и данные'])
with page[0]:
    left,right=st.columns([1,2])
    with left:show=st.selectbox('Фильтр данных',['Все','Полный Score','Неполный Score'])
    with right:query=st.text_input('Поиск тикера','')
    shown=df.copy()
    if show=='Полный Score':shown=shown[shown.score.notna()]
    if show=='Неполный Score':shown=shown[shown.score.isna() & shown.partial_score.notna()]
    if query:shown=shown[shown.ticker.str.contains(query.upper(),regex=False)]
    table=shown[['rank','ticker','ranking_score','score','partial_score','completeness','quality','growth','valuation','momentum','price','quote_date','status','validation_flags','ttm_target_end','flags']].copy()
    st.dataframe(table,hide_index=True,use_container_width=True,column_config={'ranking_score':st.column_config.NumberColumn('Рейтинговый балл',format='%.2f'),'score':st.column_config.NumberColumn('Полный Score',format='%.2f'),'partial_score':st.column_config.NumberColumn('Частичный Score',format='%.2f'),'completeness':st.column_config.ProgressColumn('Полнота, %',min_value=0,max_value=1)})
    fig=px.bar(shown.head(40),x='ticker',y='ranking_score',color='completeness',color_continuous_scale='Teal',title='Рейтинговый балл: полный или частичный')
    fig.update_layout(height=410,xaxis_title='',yaxis_title='Балл / 100')
    st.plotly_chart(fig,use_container_width=True)
with page[1]:
    ticker=st.selectbox('Компания',df.ticker.tolist())
    r=df[df.ticker==ticker].iloc[0]
    x,y,z=st.columns(3)
    x.metric('Рейтинговый балл',f"{r.ranking_score:.2f}" if pd.notna(r.ranking_score) else 'Н/Д')
    y.metric('Цена',f"${r.price:,.2f}" if pd.notna(r.price) else 'Н/Д')
    z.metric('Полнота',f"{r.completeness:.0%}")
    factor=pd.DataFrame({'Фактор':['Quality','Growth','Valuation','Momentum'],'Балл':[r.quality,r.growth,r.valuation,r.momentum]})
    st.plotly_chart(px.bar(factor,x='Фактор',y='Балл',range_y=[0,100],title='Структура Investment Score'),use_container_width=True)
    st.caption(f"Последняя дата цены: {r.quote_date} · SEC: {r.status} · Проверка: {r.validation_flags} · Флаги: {r.flags}")
    st.subheader('История цены (по запросу)')
    if st.button('Загрузить график цены',key='pricechart'):
        try:
            hist=history.loc[history.ticker.eq(ticker)].copy()
            hist['date']=pd.to_datetime(hist.date)
            hist=hist.sort_values('date').set_index('date')
            hist=hist.loc[hist.index>=hist.index.max()-pd.DateOffset(years=2)]
            if hist.empty:raise ValueError('Источник не вернул цены')
            close=hist['adj_close'];chart=go.Figure()
            chart.add_trace(go.Scatter(x=close.index,y=close,name='Цена'))
            chart.add_trace(go.Scatter(x=close.index,y=close.rolling(50).mean(),name='SMA50'))
            chart.add_trace(go.Scatter(x=close.index,y=close.rolling(200).mean(),name='SMA200'))
            st.plotly_chart(chart,use_container_width=True)
        except Exception as e:st.error(f'График недоступен: {e}')
with page[2]:
    st.subheader('Предложение по позиции — утверждает управляющий')
    t=st.selectbox('Тикер для расчёта',df.ticker.tolist(),key='position_ticker')
    r=df[df.ticker==t].iloc[0]
    col1,col2,col3=st.columns(3)
    with col1:current=st.number_input('Текущая позиция, USD',min_value=0.,value=0.,step=1000.)
    with col2:stop=st.number_input('Дистанция риска, %',min_value=1.,max_value=50.,value=12.,step=1.)/100
    with col3:sector_used=st.number_input('Текущая экспозиция сектора, USD',min_value=0.,value=0.,step=1000.)
    stock_room=max(0.,capital*max_stock-current)
    sector_room=max(0.,capital*max_sector-sector_used)
    risk_room=max(0.,capital*risk_budget/stop)
    limit=min(stock_room,sector_room,risk_room)
    trend=bool(pd.notna(r.price) and pd.notna(r.sma200) and r.price>r.sma200 and pd.notna(r.sma50) and r.sma50>r.sma200)
    # Missing or suspect inputs are never converted to a buy signal.
    fresh=False
    try:fresh=(dt.date.today()-dt.date.fromisoformat(str(r.quote_date))).days<=7
    except:pass
    fullscore=pd.notna(r.score)
    clear=str(r.status) == 'AUTOMATED_CHECK_ONLY' and (pd.isna(r.flags) or not str(r.flags).strip()) and (pd.isna(r.validation_flags) or not str(r.validation_flags).strip())
    signal='РАССМОТРЕТЬ' if fullscore and clear and fresh and trend and r.score>=60 else ('НАБЛЮДАТЬ' if fullscore else 'НЕТ СИГНАЛА — НЕДОСТАТОЧНО ДАННЫХ')
    if not fresh:signal='НЕТ СИГНАЛА — УСТАРЕВШАЯ ЦЕНА'
    st.metric('Предварительное решение',signal)
    st.metric('Максимальный дополнительный объём, USD',f'${limit:,.0f}')
    st.caption('Минимум из лимита на акцию, остатка сектора и лимита риска. Не учитывает корреляцию, ликвидность и исполнение стопа; это не гарантия максимального убытка.')
    decision=st.selectbox('Решение управляющего',['Не принято','Одобрить','Отклонить','Отложить'])
    approved=st.number_input('Одобренная сумма, USD',min_value=0.,max_value=float(max(capital,limit)),value=0.,step=1000.)
    comment=st.text_area('Обоснование / комментарий')
    if st.button('Сохранить решение в текущей сессии'):
        if decision=='Одобрить' and (approved<=0 or approved>limit):st.error('Сумма должна быть положительной и не превышать предварительный лимит.')
        else:
            st.session_state.approvals[t]={'ticker':t,'decision':decision,'approved_usd':approved,'limit_usd':limit,'comment':comment,'timestamp':dt.datetime.now().isoformat(timespec='seconds')}
            st.success('Решение сохранено в текущей сессии. Сделка не отправлена брокеру.')
with page[3]:
    st.subheader('Экспорт и журнал')
    st.download_button('Скачать текущий рейтинг CSV',df.to_csv(index=False).encode('utf-8-sig'),'investment_score.csv','text/csv')
    if st.session_state.approvals:
        approvals=pd.DataFrame(st.session_state.approvals.values())
        st.dataframe(approvals,use_container_width=True)
        st.download_button('Скачать решения управляющего CSV',approvals.to_csv(index=False).encode('utf-8-sig'),'manager_decisions.csv','text/csv')
    if not st.session_state.logs.empty:
        st.dataframe(st.session_state.logs,use_container_width=True)
        st.download_button('Скачать журнал обновления CSV',st.session_state.logs.to_csv(index=False).encode('utf-8-sig'),'sec_refresh_log.csv','text/csv')
    else:st.caption('Обновления в этой сессии пока не выполнялись.')
    st.warning('Обновление финансовых данных не заменяет ручную сверку нестандартных SEC-тегов, классов акций и банковской методологии. Для реальных клиентских портфелей нужен защищённый сервер и постоянная база данных.')
