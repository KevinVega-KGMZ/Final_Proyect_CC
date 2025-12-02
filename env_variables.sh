#!/bin/bash
set -e

# 1. Cargar variables del .env y exportarlas automaticamente
if [ -f .env ]; then
    set -a # Hace que todas las variables definidas a continuacion se exporten
    source .env
    set +a
else
    echo "Error: No se encontro el archivo .env"
    exit 1
fi

echo "Desplegando en Proyecto: $PROJECT_ID, Region: $REGION"

# 2. Verificar que las variables criticas existan
if [ -z "$PROJECT_ID" ]; then echo "Falta PROJECT_ID"; exit 1; fi

# 3. Construir la imagen (Opcional, si quieres automatizar el build tambien)
FULL_IMAGE_NAME="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:$TAG"
echo "Construyendo imagen: $FULL_IMAGE_NAME"
gcloud builds submit ./data_ingestion --tag $FULL_IMAGE_NAME

# 4. Inyectar variables y aplicar a Kubernetes
# envsubst lee el yaml, reemplaza las ${VARIABLES} y pasa el resultado a kubectl
echo "Aplicando manifiesto en Kubernetes..."
envsubst < data_ingestion/deployment.yaml | kubectl apply -f -

echo "Despliegue completado con exito."
