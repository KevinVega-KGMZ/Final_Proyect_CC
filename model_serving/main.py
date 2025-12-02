import os
import json
import base64
import datetime
from fastapi import FastAPI, Request
from google.cloud import bigquery
from river import compose, preprocessing, linear_model, metrics

PROJECT_ID = os.getenv("PROJECT_ID")
TABLE_ID = f"{PROJECT_ID}.weather_data.model_metrics"

app = FastAPI()
bq_client = bigquery.Client()

# Modelo Incremental
model = compose.Pipeline(
    preprocessing.StandardScaler(),
    linear_model.LogisticRegression()
)

# Metricas en memoria
metric_roc = metrics.ROCAUC()
metric_acc = metrics.Accuracy()
global_step = 0

# Lista de Features actualizada (SIN visib)
FEATURES = ['temp', 'dewp', 'slp', 'wdsp', 'prcp']

@app.post("/predict-and-train")
async def process_batch(request: Request):
    global global_step
    
    # Decodificar mensaje Pub/Sub (Push)
    envelope = await request.json()
    if not envelope.get("message", {}).get("data"):
        return {"status": "no_data"}
    
    pubsub_data = base64.b64decode(envelope["message"]["data"]).decode("utf-8")
    batch_rows = json.loads(pubsub_data)
    
    rows_to_insert = []
    
    for row in batch_rows:
        # Extraer Features y Target
        # Target: thunder (0 o 1)
        try:
            y = int(float(row.get('thunder', 0)))
            # Solo extraemos las features definidas en la lista
            x = {k: float(row.get(k, 0)) for k in FEATURES}
        except:
            continue
        
        # 1. Predecir (Antes de entrenar)
        y_pred_proba = model.predict_proba_one(x).get(1, 0.0)
        y_pred_class = model.predict_one(x)
        
        # 2. Actualizar Metricas
        metric_roc.update(y, y_pred_proba)
        metric_acc.update(y, y_pred_class)
        
        # 3. Entrenar (Learn)
        model.learn_one(x, y)

    global_step += 1
    
    # Guardar metricas del batch actual en BigQuery
    metric_row = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "batch_id": global_step,
        "roc_auc": metric_roc.get(),
        "accuracy": metric_acc.get(),
        "model_name": "River_Logistic_Regression"
    }
    
    errors = bq_client.insert_rows_json(TABLE_ID, [metric_row])
    if errors:
        print(f"Errores insertando en BQ: {errors}")

    return {"status": "ok", "roc": metric_roc.get()}
