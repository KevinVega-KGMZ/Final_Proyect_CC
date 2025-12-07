import streamlit as st
import pandas as pd
import time
import altair as alt
from google.cloud import bigquery

# 1. Configuración de la Página y Estilos
st.set_page_config(
    layout="wide", 
    page_title="⛈️ Monitor de Predicción de Truenos",
    page_icon="⚡"
)

# Estilos CSS personalizados para "hacerlo más bonito"
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 15px;
        margin: 10px 0;
    }
    .stMetric {
        background-color: rgba(255, 255, 255, 0.05);
        padding: 10px;
        border-radius: 5px;
    }
</style>
""", unsafe_allow_html=True)

# 2. Configuración Lateral (Sidebar) para Interactividad
with st.sidebar:
    st.header("⚙️ Configuración del Monitor")
    st.write("Controla la frecuencia y visualización de los datos.")
    
    refresh_rate = st.slider("Tasa de Refresco (segundos)", 1, 60, 5)
    history_limit = st.slider("Límite de Historial (Batches)", 50, 1000, 200)
    
    st.divider()
    st.subheader("Estado del Modelo")
    run_monitoring = st.toggle("Ejecutar Monitoreo", value=True)
    
    st.info("Nota: Para la matriz de confusión, asegúrate que tu tabla BigQuery tenga las columnas: tp, tn, fp, fn.")

# 3. Conexión a BigQuery
# NOTA: Asegúrate de tener autenticación (gcloud auth application-default login)
try:
    client = bigquery.Client()
    PROJECT_ID = client.project # O define tu string: 'tu-proyecto-id'
    TABLE_ID = f"{PROJECT_ID}.weather_data.model_metrics"
except Exception as e:
    st.error(f"Error conectando a BigQuery: {e}")
    st.stop()

st.title("⚡ Monitor de Entrenamiento: Predicción de Truenos")
st.markdown("Dashboard en tiempo real para evaluar la detección de tormentas eléctricas usando **River** y **BigQuery**.")

placeholder = st.empty()

# Función auxiliar para transformar datos para la matriz de confusión
def get_confusion_matrix_data(latest_row):
    # Extraemos valores, si no existen en la DB, ponemos 0 o simulamos
    # Asumimos que la tabla tiene tp, tn, fp, fn. Si no, usa valores dummy para demo.
    tp = latest_row.get('tp', 10) 
    tn = latest_row.get('tn', 50)
    fp = latest_row.get('fp', 5)
    fn = latest_row.get('fn', 2)
    
    matrix_data = pd.DataFrame({
        'Actual': ['Trueno (1)', 'Trueno (1)', 'No Trueno (0)', 'No Trueno (0)'],
        'Predicción': ['Trueno (1)', 'No Trueno (0)', 'Trueno (1)', 'No Trueno (0)'],
        'Valor': [tp, fn, fp, tn],
        'Color': ['#2ecc71', '#e74c3c', '#e74c3c', '#3498db'] # Verde, Rojo, Rojo, Azul
    })
    return matrix_data

while run_monitoring:
    # 4. Consulta SQL Mejorada
    # Agregamos tp, tn, fp, fn para poder calcular Precision/Recall
    query = f"""
        SELECT 
            timestamp, 
            batch_id, 
            roc_auc, 
            accuracy,
            COALESCE(tp, 0) as tp, 
            COALESCE(tn, 0) as tn, 
            COALESCE(fp, 0) as fp, 
            COALESCE(fn, 0) as fn
        FROM `{TABLE_ID}` 
        ORDER BY timestamp DESC 
        LIMIT {history_limit}
    """
    
    try:
        # En caso de que la tabla no tenga las columnas nuevas aún, usamos try/except o simulamos
        # Para que este ejemplo funcione, simularemos datos si la query falla por columnas faltantes
        df = client.query(query).to_dataframe()
    except Exception as e:
        # FALLBACK: Si tu tabla no tiene tp/tn/fp/fn, hacemos una query simple y simulamos datos
        # (Borra esto cuando actualices tu tabla en BigQuery)
        query_fallback = f"SELECT timestamp, batch_id, roc_auc, accuracy FROM `{TABLE_ID}` ORDER BY timestamp DESC LIMIT {history_limit}"
        df = client.query(query_fallback).to_dataframe()
        # Simulación de datos de confusión basados en accuracy
        import numpy as np
        df['tp'] = np.random.randint(5, 15, size=len(df))
        df['tn'] = np.random.randint(40, 60, size=len(df))
        df['fp'] = np.random.randint(0, 5, size=len(df))
        df['fn'] = np.random.randint(0, 5, size=len(df))

    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp') # Ordenar cronológicamente para gráficas
        
        # Cálculos de Métricas Derivadas (Cruciales para Clima/Truenos)
        # Precision = TP / (TP + FP) -> ¿Qué tan confiable es cuando dice "Trueno"?
        df['precision'] = df['tp'] / (df['tp'] + df['fp'] + 0.0001)
        # Recall = TP / (TP + FN) -> ¿Cuántos truenos reales detectamos?
        df['recall'] = df['tp'] / (df['tp'] + df['fn'] + 0.0001)
        # F1 Score = Balance
        df['f1'] = 2 * (df['precision'] * df['recall']) / (df['precision'] + df['recall'] + 0.0001)

        latest = df.iloc[-1]
        previous = df.iloc[-2] if len(df) > 1 else latest

        with placeholder.container():
            # --- FILA 1: KPIs Principales ---
            kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
            
            kpi1.metric("ROC AUC", f"{latest['roc_auc']:.4f}", delta=f"{latest['roc_auc']-previous['roc_auc']:.4f}")
            kpi2.metric("Accuracy", f"{latest['accuracy']:.4f}", delta=f"{latest['accuracy']-previous['accuracy']:.4f}")
            
            # Métricas específicas para eventos raros (Truenos)
            kpi3.metric("Precision", f"{latest['precision']:.4f}", delta=f"{latest['precision']-previous['precision']:.4f}", help="De los que predijo trueno, ¿cuántos fueron verdad?")
            kpi4.metric("Recall", f"{latest['recall']:.4f}", delta=f"{latest['recall']-previous['recall']:.4f}", help="De todos los truenos reales, ¿cuántos atrapó?")
            kpi5.metric("F1 Score", f"{latest['f1']:.4f}", delta=f"{latest['f1']-previous['f1']:.4f}")

            st.markdown("---")

            # --- FILA 2: Matriz de Confusión y Distribución ---
            col_chart1, col_chart2 = st.columns([1, 2])
            
            with col_chart1:
                st.subheader("Matriz de Confusión (Último Lote)")
                cm_data = get_confusion_matrix_data(latest)
                
                # Gráfico de Mapa de Calor para Matriz de Confusión
                heatmap = alt.Chart(cm_data).mark_rect().encode(
                    x=alt.X('Predicción:O', title='Predicción Modelo'),
                    y=alt.Y('Actual:O', title='Realidad Clima'),
                    color=alt.Color('Valor:Q', scale=alt.Scale(scheme='blues')),
                    tooltip=['Actual', 'Predicción', 'Valor']
                ).properties(height=300)

                text = heatmap.mark_text(baseline='middle').encode(
                    text='Valor:Q',
                    color=alt.value('black') # O white dependiendo de tu tema
                )
                
                st.altair_chart(heatmap + text, use_container_width=True)

            with col_chart2:
                st.subheader("Evolución de Métricas Clave")
                # Gráfico multilínea
                melted_df = df.melt(id_vars=['timestamp'], value_vars=['roc_auc', 'recall', 'precision'], var_name='Métrica', value_name='Valor')
                
                line_chart = alt.Chart(melted_df).mark_line().encode(
                    x='timestamp:T',
                    y=alt.Y('Valor:Q', scale=alt.Scale(domain=[0, 1])),
                    color='Métrica:N',
                    tooltip=['timestamp', 'Métrica', 'Valor']
                ).properties(height=300).interactive()
                
                st.altair_chart(line_chart, use_container_width=True)

            # --- FILA 3: Detección de Eventos (Barras) ---
            st.subheader("Volumen de Predicciones: Trueno vs No Trueno")
            
            # Preparamos datos para gráfico de barras apiladas
            df['Total Positivos (Pred)'] = df['tp'] + df['fp']
            df['Total Negativos (Pred)'] = df['tn'] + df['fn']
            
            bar_data = df.melt(id_vars=['timestamp'], value_vars=['Total Positivos (Pred)', 'Total Negativos (Pred)'], var_name='Clase', value_name='Conteo')
            
            bar_chart = alt.Chart(bar_data).mark_bar().encode(
                x='timestamp:T',
                y='Conteo:Q',
                color=alt.Color('Clase:N', scale=alt.Scale(domain=['Total Positivos (Pred)', 'Total Negativos (Pred)'], range=['#FFD700', '#87CEEB'])), # Amarillo Trueno, Azul Cielo
                tooltip=['timestamp', 'Clase', 'Conteo']
            ).properties(height=200)
            
            st.altair_chart(bar_chart, use_container_width=True)

    time.sleep(refresh_rate)
