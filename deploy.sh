#!/bin/bash
set -e

# --- VARIABLES GLOBALES ---
export PROJECT_ID=$(gcloud config get-value project)
export REGION="us-central1"
export PROJECT_NUM=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

# Variables Dataflow
export BUCKET_NAME="${PROJECT_ID}-dataflow-staging"
export INPUT_TOPIC="noaa-raw"
export OUTPUT_TOPIC="noaa-clean-for-ml"

# Variables GKE y Artifacts
export APP_NAME="streaming-simulator"
export REPO_NAME="mlops-repo"
export CLUSTER_NAME="mlops-cluster"
export K8S_NAMESPACE="default"
export K8S_SA="default"
export GSA_NAME="mlops-sa"
export GSA_EMAIL="$GSA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

# Variables BigQuery
export DATASET_NAME="weather_data"
export METRICS_TABLE="model_metrics"

echo ">>> Configurando Proyecto: $PROJECT_ID ($PROJECT_NUM)"

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

# 2. Recursos Base (PubSub, Storage, BQ)
gcloud pubsub topics create $INPUT_TOPIC || true
gcloud pubsub topics create $OUTPUT_TOPIC || true
gsutil mb -l $REGION gs://$BUCKET_NAME || true

bq mk --dataset $DATASET_NAME || true
bq mk --table $DATASET_NAME.$METRICS_TABLE \
    timestamp:TIMESTAMP,batch_id:INTEGER,roc_auc:FLOAT,accuracy:FLOAT,model_name:STRING || true

# 3. Permisos IAM (Dataflow y GKE)
echo ">>> Aplicando Permisos IAM..."
# Agente Dataflow
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:service-${PROJECT_NUM}@dataflow-service-producer-prod.iam.gserviceaccount.com" \
    --role="roles/dataflow.serviceAgent"

# Compute SA (Workers)
COMPUTE_SA="${PROJECT_NUM}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$COMPUTE_SA --role=roles/dataflow.worker
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$COMPUTE_SA --role=roles/storage.objectAdmin
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$COMPUTE_SA --role=roles/dataflow.admin
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$COMPUTE_SA --role=roles/pubsub.editor

# GSA para GKE
gcloud iam service-accounts create $GSA_NAME --display-name="MLOps Service Account" || true
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$GSA_EMAIL" --role="roles/bigquery.jobUser"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$GSA_EMAIL" --role="roles/bigquery.dataViewer"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$GSA_EMAIL" --role="roles/pubsub.publisher"

# 4. Desplegar Listener (Dataflow - Cleaning)
echo ">>> Iniciando Dataflow Job..."
JOB_NAME="cleaner-listener-$(date +%Y%m%d-%H%M%S)"
pip install apache-beam[gcp]
python3 data_cleaning/main.py \
  --project_id $PROJECT_ID \
  --job_name $JOB_NAME \
  --input_topic "projects/$PROJECT_ID/topics/$INPUT_TOPIC" \
  --output_topic "projects/$PROJECT_ID/topics/$OUTPUT_TOPIC" \
  --staging_bucket "gs://$BUCKET_NAME" \
  --region $REGION \
  --disk_size_gb 30 \
  --max_num_workers 5 \
  --worker_machine_type n1-standard-2

# 5. Desplegar Generador (GKE)
echo ">>> Construyendo y Desplegando Generador en GKE..."
gcloud artifacts repositories create $REPO_NAME \
    --repository-format=docker \
    --location=$REGION \
    --description="Repositorio MLOps" || true

# Build Generator
gcloud builds submit ./data_ingestion \
    --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$APP_NAME:v1

# Cluster Create
gcloud container clusters create-auto $CLUSTER_NAME --region $REGION --project $PROJECT_ID || true
gcloud container clusters get-credentials $CLUSTER_NAME --region $REGION

# Workload Identity Link
gcloud iam service-accounts add-iam-policy-binding $GSA_EMAIL \
    --role roles/iam.workloadIdentityUser \
    --member "serviceAccount:$PROJECT_ID.svc.id.goog[$K8S_NAMESPACE/$K8S_SA]"

kubectl annotate serviceaccount $K8S_SA \
    iam.gke.io/gcp-service-account=$GSA_EMAIL \
    --overwrite

# Reemplazar variables en deployment.yaml y aplicar
sed -e "s|IMAGE_PLACEHOLDER|$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$APP_NAME:v1|g" \
    -e "s|PROJECT_ID_PLACEHOLDER|$PROJECT_ID|g" \
    ./data_ingestion/deployment.yaml | kubectl apply -f -

# 6. Desplegar Modelo (Cloud Run)
echo ">>> Desplegando Model Serving en Cloud Run..."
gcloud builds submit ./model_serving --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/model-serving:v1

gcloud run deploy model-serving \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/model-serving:v1 \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars PROJECT_ID=$PROJECT_ID

# Crear suscripcion Push para conectar Dataflow -> Cloud Run
SERVICE_URL=$(gcloud run services describe model-serving --region $REGION --format 'value(status.url)')
gcloud pubsub subscriptions create sub-model-serving \
    --topic $OUTPUT_TOPIC \
    --push-endpoint "$SERVICE_URL/predict-and-train" \
    --ack-deadline 600 || true

# 7. Desplegar Dashboard (Cloud Run)
echo ">>> Desplegando Dashboard en Cloud Run..."
gcloud builds submit ./dashboard --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/dashboard:v1

gcloud run deploy dashboard \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/dashboard:v1 \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars PROJECT_ID=$PROJECT_ID

echo ">>> DESPLIEGUE FINALIZADO"
echo "Dataflow Job: Corriendo"
echo "Generador: Corriendo en GKE"
echo "Modelo URL (recibe datos): $SERVICE_URL"
echo "Dashboard URL (visualizacion):"
gcloud run services describe dashboard --region $REGION --format 'value(status.url)'
