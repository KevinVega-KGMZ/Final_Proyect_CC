#!/bin/bash
set -e

echo ">>> Iniciando Script de Despliegue MLOps..."

# 1. Deteccion automatica del entorno
export PROJECT_ID=$(gcloud config get-value project)
export PROJECT_NUM=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
export REGION="us-central1"

# 2. Definicion de Variables del Proyecto
export BUCKET_NAME="${PROJECT_ID}-dataflow-staging"
export INPUT_TOPIC="noaa-raw"
export OUTPUT_TOPIC="noaa-clean-for-ml"
export REPO_NAME="mlops-repo"
export APP_NAME="streaming-simulator"
export CLUSTER_NAME="mlops-cluster"
export GSA_NAME="mlops-sa"
export GSA_EMAIL="$GSA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
export K8S_NAMESPACE="default"
export K8S_SA="default"

# 3. Generacion del archivo .env (Para uso de envsubst y referencia)
echo ">>> Generando archivo .env..."
cat <<EOF > .env
PROJECT_ID=$PROJECT_ID
PROJECT_NUM=$PROJECT_NUM
REGION=$REGION
BUCKET_NAME=$BUCKET_NAME
INPUT_TOPIC=$INPUT_TOPIC
OUTPUT_TOPIC=$OUTPUT_TOPIC
REPO_NAME=$REPO_NAME
APP_NAME=$APP_NAME
CLUSTER_NAME=$CLUSTER_NAME
GSA_EMAIL=$GSA_EMAIL
EOF

# Cargar variables para asegurar que esten disponibles para envsubst
source .env

echo ">>> Configuracion: Proyecto $PROJECT_ID ($REGION)"

# 4. Habilitar APIs
gcloud services enable \
    dataflow.googleapis.com \
    artifactregistry.googleapis.com \
    container.googleapis.com \
    pubsub.googleapis.com \
    bigquery.googleapis.com \
    storage.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com

# 5. Recursos Base
gcloud pubsub topics create $INPUT_TOPIC || true
gcloud pubsub topics create $OUTPUT_TOPIC || true
gsutil mb -l $REGION gs://$BUCKET_NAME || true

bq mk --dataset weather_data || true
bq mk --table weather_data.model_metrics \
    timestamp:TIMESTAMP,batch_id:INTEGER,roc_auc:FLOAT,accuracy:FLOAT,model_name:STRING || true

gcloud artifacts repositories create $REPO_NAME \
    --repository-format=docker \
    --location=$REGION \
    --description="Repositorio MLOps" || true

# 6. Permisos IAM
echo ">>> Configurando IAM..."
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

# GKE Service Account
gcloud iam service-accounts create $GSA_NAME --display-name="MLOps SA" || true
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$GSA_EMAIL" --role="roles/bigquery.jobUser"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$GSA_EMAIL" --role="roles/bigquery.dataViewer"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$GSA_EMAIL" --role="roles/pubsub.publisher"

# 7. Desplegar Dataflow (ETL)
echo ">>> Desplegando Dataflow..."
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
  --max_num_workers 4 \
  --worker_machine_type n1-standard-2

# 8. Desplegar Generador (GKE) con envsubst
echo ">>> Desplegando Generador en GKE..."
gcloud builds submit ./data_ingestion \
    --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$APP_NAME:v1

gcloud container clusters create-auto $CLUSTER_NAME --region $REGION --project $PROJECT_ID || true
gcloud container clusters get-credentials $CLUSTER_NAME --region $REGION

# Workload Identity
gcloud iam service-accounts add-iam-policy-binding $GSA_EMAIL \
    --role roles/iam.workloadIdentityUser \
    --member "serviceAccount:$PROJECT_ID.svc.id.goog[$K8S_NAMESPACE/$K8S_SA]"

kubectl annotate serviceaccount $K8S_SA \
    iam.gke.io/gcp-service-account=$GSA_EMAIL \
    --overwrite

# AQUI ESTA LA MAGIA: envsubst reemplaza las variables en el YAML y lo aplica
envsubst < data_ingestion/deployment.yaml | kubectl apply -f -

# 9. Desplegar Cloud Run (Model & Dashboard)
echo ">>> Desplegando Cloud Run..."
# Modelo
gcloud builds submit ./model_serving --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/model-serving:v1
gcloud run deploy model-serving \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/model-serving:v1 \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars PROJECT_ID=$PROJECT_ID

SERVICE_URL=$(gcloud run services describe model-serving --region $REGION --format 'value(status.url)')
gcloud pubsub subscriptions create sub-model-training \
    --topic $OUTPUT_TOPIC \
    --push-endpoint "$SERVICE_URL/predict-and-train" \
    --ack-deadline 600 || true

# Dashboard
gcloud builds submit ./dashboard --tag $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/dashboard:v1
gcloud run deploy dashboard \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/dashboard:v1 \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars PROJECT_ID=$PROJECT_ID

echo ">>> DESPLIEGUE FINALIZADO EXITOSAMENTE"
echo "Generador: Corriendo en GKE ($CLUSTER_NAME)"
echo "ETL: Dataflow Job ID $JOB_NAME"
echo "Modelo API: $SERVICE_URL"
echo "Dashboard URL:"
gcloud run services describe dashboard --region $REGION --format 'value(status.url)'
