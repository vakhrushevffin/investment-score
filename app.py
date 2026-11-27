import streamlit as st
st.set_page_config(page_title='Investment Score',page_icon='📊',layout='wide')
st.navigation([
    st.Page('ui/fundamental_analysis.py',title='Fundamental Analysis',default=True),
    st.Page('pages/1_Technical_Risk_Monitor.py',title='Technical Risk Monitor'),
    st.Page('pages/2_Options_Market_Monitor.py',title='Options Market Monitor'),
]).run()
