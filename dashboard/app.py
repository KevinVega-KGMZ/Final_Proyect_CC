import streamlit as st
import pandas as pd
import time
import altair as alt
import numpy as np
from google.cloud import bigquery

# 1. Configuración de la página
st.set_page_config(
    layout="wide", 
    page_title="Monitor de Prediccion de Truenos"
)

# Estilos CSS para mejorar la apariencia sin usar emojis
st.markdown("""
<style>
    .metric-container {
        background-color: #f8f9fa;
        padding: 15px;
        border-radius: 5px;
        border: 1px solid #dee2e6;
    }
    .stMetric {
        background-color: transparent;
    }
    h1, h2, h3 {
        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# 2. Barra lateral de configuración
with st.sidebar:
    st.header("Panel de Control")
    st.markdown("Ajustes de visualización y frecuencia.")
    
    refresh_rate = st.slider("Actualización (segundos)", min_value=1, max_value=60, value=5)
    history_limit = st.slider("Historial de lotes", min_value=50, max_value=500, value=100)
    
    st.divider()
    st.subheader("Estado del Sistema")
    is_running = st.toggle("Monitoreo Activo", value=True)
    
    st.info("Nota: Se requieren columnas tp, tn, fp, fn en BigQuery para la matriz exacta.")

# 3. Inicialización de cliente BigQuery
# Asegúrate de tener las credenciales configuradas en tu entorno
try:
    client = bigquery.Client()
    PROJECT_ID = client.project 
    TABLE_ID = f"{PROJECT_ID}.weather_data.model_metrics"
except Exception:
    # Fallback silencioso si no hay credenciales locales para demostración visual
    client = None

st.title("Monitor de Entrenamiento Continuo")
st.markdown("Visualización en tiempo real de métricas de detección de tormentas eléctricas.")

placeholder = st.empty()

def get_data():
    """Obtiene datos de BQ o genera datos simulados si faltan columnas."""
    if client:
        # Intentamos traer las métricas de confusión
        query = f"""
            SELECT 
                timestamp, batch_id, roc_auc, accuracy,
                COALESCE(tp, 0) as tp, COALESCE(tn, 0) as tn, 
                COALESCE(fp, 0) as fp, COALESCE(fn, 0) as fn
            FROM `{TABLE_ID}` 
            ORDER BY timestamp DESC 
            LIMIT {history_limit}
        """
        try:
            return client.query(query).to_dataframe()
        except Exception:
            pass # Si falla, pasamos a simulación

    # Simulación de datos (si no hay conexión o faltan columnas)
    dates = pd.date_range(end=pd.Timestamp.now(), periods=history_limit, freq='min')
    data = {
        'timestamp': dates,
        'batch_id': range(history_limit),
        'roc_auc': np.random.uniform(0.7, 0.95, history_limit),
        'accuracy': np.random.uniform(0.85, 0.99, history_limit),
        'tp': np.random.randint(5, 20, history_limit),
        'tn': np.random.randint(50, 80, history_limit),
        'fp': np.random.randint(1, 10, history_limit),
        'fn': np.random.randint(1, 8, history_limit)
    }
    return pd.DataFrame(data)

def format_confusion_matrix(row):
    """Transforma una fila de datos en formato largo para el mapa de calor."""
    return pd.DataFrame([
        {'Real': 'Trueno (1)', 'Predicho': 'Trueno (1)', 'Valor': row['tp'], 'Tipo': 'Verdadero Positivo'},
        {'Real': 'Trueno (1)', 'Predicho': 'No Trueno (0)', 'Valor': row['fn'], 'Tipo': 'Falso Negativo'},
        {'Real': 'No Trueno (0)', 'Predicho': 'Trueno (1)', 'Valor': row['fp'], 'Tipo': 'Falso Positivo'},
        {'Real': 'No Trueno (0)', 'Predicho': 'No Trueno (0)', 'Valor': row['tn'], 'Tipo': 'Verdadero Negativo'}
    ])

# 4. Bucle principal
while is_running:
    df = get_data()
    
    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp')
        
        # Cálculos de métricas adicionales (Precision y Recall son vitales para truenos)
        # Recall (Sensibilidad): ¿Cuántos truenos reales detectamos?
        df['recall'] = df['tp'] / (df['tp'] + df['fn'] + 0.00001)
        # Precision: Cuando predecimos trueno, ¿es verdad?
        df['precision'] = df['tp'] / (df['tp'] + df['fp'] + 0.00001)
        # F1 Score: Balance entre ambos
        df['f1'] = 2 * (df['precision'] * df['recall']) / (df['precision'] + df['recall'] + 0.00001)

        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest

        with placeholder.container():
            # SECCIÓN 1: KPIs Principales
            st.subheader("Métricas del Último Lote")
            k1, k2, k3, k4, k5 = st.columns(5)
            
            k1.metric("ROC AUC", f"{latest['roc_auc']:.3f}", f"{latest['roc_auc']-prev['roc_auc']:.3f}")
            k2.metric("Accuracy", f"{latest['accuracy']:.3f}", f"{latest['accuracy']-prev['accuracy']:.3f}")
            k3.metric("Precision", f"{latest['precision']:.3f}", f"{latest['precision']-prev['precision']:.3f}", help="Calidad de las alertas positivas")
            k4.metric("Recall", f"{latest['recall']:.3f}", f"{latest['recall']-prev['recall']:.3f}", help="Capacidad de detectar eventos reales")
            k5.metric("F1 Score", f"{latest['f1']:.3f}", f"{latest['f1']-prev['f1']:.3f}")
            
            st.markdown("---")

            # SECCIÓN 2: Matriz de Confusión y Tendencias
            col_left, col_right = st.columns([1, 2])
            
            with col_left:
                st.markdown("#### Matriz de Confusión Actual")
                cm_data = format_confusion_matrix(latest)
                
                # Gráfico Heatmap
                base = alt.Chart(cm_data).encode(
                    x=alt.X('Predicho:O', title='Predicción del Modelo'),
                    y=alt.Y('Real:O', title='Realidad (Clima)')
                )
                
                heatmap = base.mark_rect().encode(
                    color=alt.Color('Valor:Q', scale=alt.Scale(scheme='blues'), legend=None)
                )
                
                text = base.mark_text(baseline='middle', size=16).encode(
                    text='Valor:Q',
                    color=alt.value('black')
                )
                
                st.altair_chart((heatmap + text).properties(height=300), use_container_width=True)

            with col_right:
                st.markdown("#### Evolución: Precision vs Recall")
                # Gráfico de líneas multivariable
                line_data = df.melt(id_vars=['timestamp'], value_vars=['recall', 'precision', 'roc_auc'], var_name='Métrica', value_name='Score')
                
                lines = alt.Chart(line_data).mark_line(point=False).encode(
                    x='timestamp:T',
                    y=alt.Y('Score:Q', scale=alt.Scale(domain=[0, 1])),
                    color=alt.Color('Métrica:N', scale=alt.Scale(scheme='category10')),
                    tooltip=['timestamp', 'Métrica', 'Score']
                ).properties(height=300).interactive()
                
                st.altair_chart(lines, use_container_width=True)

            # SECCIÓN 3: Volumen de Alertas
            st.markdown("#### Volumen de Predicciones (Trueno vs No Trueno)")
            
            df['Positivos (Pred)'] = df['tp'] + df['fp']
            df['Negativos (Pred)'] = df['tn'] + df['fn']
            
            vol_data = df.melt(id_vars=['timestamp'], value_vars=['Positivos (Pred)', 'Negativos (Pred)'], var_name='Clase', value_name='Conteo')
            
            bars = alt.Chart(vol_data).mark_bar().encode(
                x='timestamp:T',
                y='Conteo:Q',
                color=alt.Color('Clase:N', scale=alt.Scale(range=['#1f77b4', '#ff7f0e'])),
                tooltip=['timestamp', 'Clase', 'Conteo']
            ).properties(height=200)
            
            st.altair_chart(bars, use_container_width=True)

    time.sleep(refresh_rate)
