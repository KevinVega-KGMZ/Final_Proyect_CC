import streamlit as st
import pandas as pd
import time
import altair as alt
from google.cloud import bigquery

st.set_page_config(layout="wide", page_title="Monitor de Modelo en Tiempo Real")
st.title("Monitor de Entrenamiento Continuo (River)")

client = bigquery.Client()
PROJECT_ID = client.project
TABLE_ID = f"{PROJECT_ID}.weather_data.model_metrics"

placeholder = st.empty()

while True:
    query = f"""
        SELECT timestamp, batch_id, roc_auc, accuracy 
        FROM `{TABLE_ID}` 
        ORDER BY timestamp DESC 
        LIMIT 200
    """
    df = client.query(query).to_dataframe()
    
    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        curr_roc = df.iloc[0]['roc_auc']
        curr_acc = df.iloc[0]['accuracy']
        
        with placeholder.container():
            kpi1, kpi2 = st.columns(2)
            kpi1.metric("ROC AUC Actual", f"{curr_roc:.4f}")
            kpi2.metric("Accuracy Actual", f"{curr_acc:.4f}")

            chart = alt.Chart(df).mark_line(point=True).encode(
                x='timestamp',
                y='roc_auc',
                tooltip=['batch_id', 'roc_auc', 'accuracy']
            ).properties(title="Evolucion ROC AUC en Tiempo Real")
            
            st.altair_chart(chart, use_container_width=True)

    time.sleep(3)
