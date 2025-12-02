# Real-Time Thunder Prediction System (MLOps on GCP)

Este proyecto implementa un pipeline de MLOps de extremo a extremo en Google Cloud Platform. El sistema simula un flujo de datos meteorológicos en tiempo real, los limpia, entrena un modelo incremental y visualiza las métricas de rendimiento en vivo.

### Arquitectura del Flujo
1.  **Ingestión (GKE):** Simula datos en streaming leyendo históricamente de BigQuery (2015-2025) y publicándolos en Pub/Sub.
2.  **ETL (Dataflow):** Lee de Pub/Sub, limpia datos, imputa valores faltantes y calcula medias móviles.
3.  **Model Serving (Cloud Run):** Consume datos limpios, entrena un modelo de Regresión Logística incremental (River) y guarda métricas.
4.  **Monitoring (Streamlit):** Dashboard en vivo que lee las métricas de entrenamiento desde BigQuery.

---

## 🚀 Guía de Despliegue Paso a Paso

Sigue estos pasos secuenciales en tu terminal (se recomienda usar **Google Cloud Shell**).

### 1. Configuración del Entorno
Primero, definimos las variables de entorno que se usarán en todo el proyecto.

```bash
# Variables de Proyecto y Región
export PROJECT_ID=$(gcloud config get-value project)
export REGION="us-central1"
export PROJECT_NUM=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

# Nombres de Recursos
export BUCKET_NAME="${PROJECT_ID}-dataflow-staging"
export INPUT_TOPIC="noaa-raw"
export OUTPUT_TOPIC="noaa-clean-for-ml"
export REPO_NAME="mlops-repo"
export CLUSTER_NAME="mlops-cluster"

echo "Configurando Proyecto: $PROJECT_ID"
gcloud config set project $PROJECT_ID
```

### 2. Infraestructura Base y Permisos
Habilitamos las APIs necesarias y creamos los recursos de almacenamiento y mensajería.

```bash
# 1. Habilitar APIs
gcloud services enable \
    dataflow.googleapis.com \
    artifactregistry.googleapis.com \
    container.googleapis.com \
    pubsub.googleapis.com \
    bigquery.googleapis.com \
    storage.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com

# 2. Crear Topics de Pub/Sub
gcloud pubsub topics create $INPUT_TOPIC || true
gcloud pubsub topics create $OUTPUT_TOPIC || true

# 3. Crear Bucket para Dataflow
gsutil mb -l $REGION gs://$BUCKET_NAME || true

# 4. Crear Dataset y Tabla en BigQuery
bq mk --dataset weather_data || true
bq mk --table weather_data.model_metrics \
    timestamp:TIMESTAMP,batch_id:INTEGER,roc_auc:FLOAT,accuracy:FLOAT,model_name:STRING || true

# 5. Crear Repositorio de Docker
gcloud artifacts repositories create $REPO_NAME \
    --repository-format=docker \
    --location=$REGION \
    --description="Repositorio MLOps" || true
```

### 3. Configuración de IAM (Gestión de Identidad)
Este paso es crítico. Asignamos permisos para que Dataflow y Kubernetes puedan interactuar con otros servicios.

```bash
# Permisos para el Agente de Servicio de Dataflow
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:service-${PROJECT_NUM}@dataflow-service-producer-prod.iam.gserviceaccount.com" \
    --role="roles/dataflow.serviceAgent"

# Permisos para la Cuenta de Servicio Compute (usada por los Workers)
COMPUTE_SA="${PROJECT_NUM}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$COMPUTE_SA --role=roles/dataflow.worker
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$COMPUTE_SA --role=roles/storage.objectAdmin
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$COMPUTE_SA --role=roles/dataflow.admin
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$COMPUTE_SA --role=roles/pubsub.editor

# Crear Cuenta de Servicio para GKE (Generator)
export GSA_NAME="mlops-sa"
gcloud iam service-accounts create $GSA_NAME --display-name="MLOps Service Account" || true
export GSA_EMAIL="$GSA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

# Dar permisos a la cuenta de GKE
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$GSA_EMAIL" --role="roles/bigquery.jobUser"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$GSA_EMAIL" --role="roles/bigquery.dataViewer"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$GSA_EMAIL" --role="roles/pubsub.publisher"
```

---

### 4. Despliegue del Generador de Datos (GKE)
Este componente simula la llegada de datos. Se despliega en Kubernetes.

```bash
# 1. Construir la imagen Docker
gcloud builds submit ./data_ingestion \
    --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/streaming-simulator:v1

# 2. Crear Cluster GKE (Puede tardar 5-10 minutos)
gcloud container clusters create-auto $CLUSTER_NAME \
    --region $REGION \
    --project $PROJECT_ID

# 3. Obtener credenciales del cluster
gcloud container clusters get-credentials $CLUSTER_NAME --region $REGION

# 4. Vincular Kubernetes SA con Google SA (Workload Identity)
gcloud iam service-accounts add-iam-policy-binding $GSA_EMAIL \
    --role roles/iam.workloadIdentityUser \
    --member "serviceAccount:$PROJECT_ID.svc.id.goog[default/default]"

kubectl annotate serviceaccount default \
    iam.gke.io/gcp-service-account=$GSA_EMAIL \
    --overwrite

# 5. Desplegar en el cluster
# Nota: Reemplazamos los placeholders en el YAML antes de aplicar
sed -e "s|IMAGE_PLACEHOLDER|$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/streaming-simulator:v1|g" \
    -e "s|PROJECT_ID_PLACEHOLDER|$PROJECT_ID|g" \
    ./data_ingestion/deployment.yaml | kubectl apply -f -
```

---

### 5. Despliegue del ETL (Dataflow)
Iniciamos el trabajo de limpieza de datos. Este job correrá indefinidamente procesando el stream.

```bash
# 1. Instalar Apache Beam con soporte GCP
pip install apache-beam[gcp]

# 2. Lanzar el Job
export JOB_NAME="cleaner-listener-$(date +%Y%m%d-%H%M%S)"

python3 data_cleaning/main.py \
  --project_id $PROJECT_ID \
  --job_name $JOB_NAME \
  --input_topic "projects/$PROJECT_ID/topics/$INPUT_TOPIC" \
  --output_topic "projects/$PROJECT_ID/topics/$OUTPUT_TOPIC" \
  --staging_bucket "gs://$BUCKET_NAME" \
  --region $REGION \
  --disk_size_gb 30 \
  --max_num_workers 4 \
  --worker_machine_type n1-standard-2

echo "Job de Dataflow enviado: $JOB_NAME"
```

---

### 6. Despliegue del Modelo (Cloud Run)
Desplegamos la API que recibe los datos limpios y entrena el modelo.

```bash
# 1. Construir imagen
gcloud builds submit ./model_serving \
    --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/model-serving:v1

# 2. Desplegar servicio
gcloud run deploy model-serving \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/model-serving:v1 \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars PROJECT_ID=$PROJECT_ID

# 3. Conectar Pub/Sub con Cloud Run (Push Subscription)
# Esto hace que cada mensaje limpio dispare el entrenamiento
SERVICE_URL=$(gcloud run services describe model-serving --region $REGION --format 'value(status.url)')

gcloud pubsub subscriptions create sub-model-training \
    --topic $OUTPUT_TOPIC \
    --push-endpoint "$SERVICE_URL/predict-and-train" \
    --ack-deadline 600 || true
```

---

### 7. Despliegue del Dashboard (Cloud Run)
Finalmente, desplegamos la interfaz visual.

```bash
# 1. Construir imagen
gcloud builds submit ./dashboard \
    --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/dashboard:v1

# 2. Desplegar servicio
gcloud run deploy dashboard \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/dashboard:v1 \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars PROJECT_ID=$PROJECT_ID

# 3. Obtener URL
echo ">>> ACCEDE AL DASHBOARD AQUI:"
gcloud run services describe dashboard --region $REGION --format 'value(status.url)'
```

---

### Verificación y Monitoreo

1.  **Dashboard:** Haz clic en la URL generada en el paso 7. Deberías ver gráficas actualizándose cada pocos segundos.
2.  **Dataflow:** Ve a la consola de Google Cloud -> Dataflow. Deberías ver el job en estado "Running" y datos fluyendo por los pasos.
3.  **Logs:**
    *   Para ver el generador: `kubectl logs -l app=streaming-simulator`
    *   Para ver el modelo: Consola -> Cloud Run -> model-serving -> Logs.

### Limpieza de Recursos (Clean Up)
Para evitar costos, una vez finalices la práctica, elimina el proyecto o los recursos:

```bash
# Opción A: Borrar todo el proyecto (Recomendado)
gcloud projects delete $PROJECT_ID

# Opción B: Borrar recursos individuales
gcloud container clusters delete $CLUSTER_NAME --region $REGION --quiet
gcloud dataflow jobs cancel $(gcloud dataflow jobs list --status=active --format="value(JOB_ID)") --region $REGION
gcloud run services delete model-serving --region $REGION --quiet
gcloud run services delete dashboard --region $REGION --quiet
```
