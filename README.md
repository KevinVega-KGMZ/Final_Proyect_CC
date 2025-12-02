```markdown
# Real-Time Thunder Prediction System (MLOps on GCP)

Este repositorio contiene la implementación completa de un pipeline de MLOps de extremo a extremo en Google Cloud Platform. El sistema simula un flujo de datos meteorológicos en tiempo real, los procesa, limpia y entrena un modelo de Machine Learning incremental para predecir la ocurrencia de truenos.

## Descripción de la Arquitectura

El flujo de datos atraviesa los siguientes componentes en la nube:

1.  **Ingestión de Datos (GKE):** Un generador en Python desplegado en Google Kubernetes Engine lee datos históricos (2015-2025) de BigQuery y los publica secuencialmente en un tópico de Pub/Sub para simular un entorno de streaming.
2.  **ETL y Limpieza (Dataflow):** Un pipeline de Apache Beam consume los datos crudos, imputa valores faltantes (medias móviles para presión, ceros para precipitación) y filtra registros corruptos.
3.  **Entrenamiento de Modelo (Cloud Run):** Una API construida con FastAPI y River (Online Machine Learning) consume los datos limpios. Con cada nuevo evento, el modelo actualiza sus pesos (entrenamiento incremental) y guarda métricas de rendimiento en BigQuery.
4.  **Monitoreo (Cloud Run):** Un dashboard interactivo basado en Streamlit visualiza las métricas de ROC-AUC y Accuracy en tiempo real.

## Estructura del Repositorio

```text
.
├── data_ingestion/       # Código fuente del generador y manifiesto Kubernetes
│   ├── Dockerfile
│   ├── main.py
│   └── deployment.yaml
├── data_cleaning/        # Pipeline ETL de Apache Beam
│   └── main.py
├── model_serving/        # API de Entrenamiento y Predicción
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
├── dashboard/            # Visualización de Métricas
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
├── deploy.sh             # Script maestro de automatización
└── README.md             # Documentación del proyecto
```

## Requisitos Previos

*   Cuenta de Google Cloud Platform activa.
*   Google Cloud Shell (recomendado) o Google Cloud SDK instalado localmente.
*   Permisos de Propietario o Editor en el proyecto de GCP seleccionado.

## Guía de Despliegue Automatizado

El despliegue se gestiona mediante el script `deploy.sh`, que automatiza la creación de infraestructura, construcción de imágenes Docker y configuración de servicios.

### Pasos para el Despliegue

1.  **Clonar el repositorio:**
    Asegúrese de tener todos los archivos en su entorno de trabajo (Cloud Shell).

2.  **Otorgar permisos de ejecución al script:**
    Ejecute el siguiente comando en la terminal para hacer ejecutable el script de despliegue.

    ```bash
    chmod +x deploy.sh
    ```

3.  **Ejecutar el despliegue:**
    Inicie el proceso. El script detectará automáticamente su Project ID y región.

    ```bash
    ./deploy.sh
    ```

    *Nota: El proceso completo puede tardar entre 10 y 15 minutos, ya que incluye la creación de un clúster de Kubernetes y la compilación de contenedores.*

### ¿Qué hace el script deploy.sh?

*   Habilita las APIs necesarias (Dataflow, GKE, Cloud Run, Artifact Registry, etc.).
*   Crea los tópicos de Pub/Sub y los Datasets de BigQuery.
*   Configura las cuentas de servicio (IAM) y los permisos necesarios.
*   Genera un archivo `.env` dinámico con las variables del proyecto.
*   Compila las imágenes Docker y las sube a Artifact Registry.
*   Despliega el pipeline de Dataflow.
*   Despliega el generador en GKE inyectando las variables de entorno mediante `envsubst`.
*   Despliega los servicios de Cloud Run y configura las suscripciones Push.

## Configuración Técnica y Variables

El sistema utiliza inyección de variables de entorno para mantener la portabilidad del código. El archivo `data_ingestion/deployment.yaml` utiliza placeholders con el formato `${VARIABLE}`.

Durante el despliegue, el script genera un archivo `.env` y utiliza la herramienta `envsubst` para reemplazar estos valores dinámicamente antes de aplicar el manifiesto en Kubernetes:

```bash
# Ejemplo del comando interno ejecutado por el script
envsubst < data_ingestion/deployment.yaml | kubectl apply -f -
```

Esto asegura que el despliegue funcione correctamente en cualquier Proyecto de Google Cloud sin necesidad de editar manualmente los archivos YAML.

## Verificación y Monitoreo

Una vez finalizado el script `deploy.sh`, la consola mostrará las URLs de los servicios.

1.  **Dashboard de Métricas:** Acceda a la URL del servicio `dashboard` proporcionada al final de la ejecución para ver el rendimiento del modelo.
2.  **Estado del Pipeline:** Visite la consola de Google Cloud -> Dataflow para verificar que el trabajo `cleaner-listener` esté en estado "Running".
3.  **Logs del Generador:** Puede verificar que los datos se están enviando correctamente revisando los logs del clúster GKE:
    ```bash
    kubectl logs -l app=streaming-simulator
    ```

## Limpieza de Recursos

Para evitar cargos innecesarios en su facturación de Google Cloud, elimine los recursos una vez finalizada la prueba.

Opción recomendada (eliminar proyecto completo):
```bash
gcloud projects delete $PROJECT_ID
```

Opción manual (eliminar recursos individuales):
```bash
gcloud container clusters delete mlops-cluster --region us-central1 --quiet
gcloud run services delete model-serving --region us-central1 --quiet
gcloud run services delete dashboard --region us-central1 --quiet
gcloud dataflow jobs cancel $(gcloud dataflow jobs list --status=active --format="value(JOB_ID)") --region us-central1
```
```
