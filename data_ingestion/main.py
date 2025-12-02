import time
import json
import logging
import os
import sys
from google.cloud import bigquery
from google.cloud import pubsub_v1

# Environment Variable Configurations
PROJECT_ID = os.getenv("PROJECT_ID")
TOPIC_ID = os.getenv("TOPIC_ID")
# Valor por defecto si no existe la variable, para evitar errores
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 10000)) 

# Log Configuration
logging.basicConfig(stream=sys.stdout, level=logging.INFO, 
                    format='%(asctime)s - [Streaming Simulator] - %(message)s')

# --- MODIFICACIÓN PRINCIPAL AQUÍ ---
# Usamos el comodín (*) y _TABLE_SUFFIX para consultar desde 2015 hasta 2025
QUERY = """
    SELECT 
        year, mo, da, 
        temp, dewp, slp, stp, wdsp, mxpsd, max, min, prcp, sndp, thunder
    FROM `bigquery-public-data.noaa_gsod.gsod*`
    WHERE _TABLE_SUFFIX BETWEEN '2015' AND '2025'
    ORDER BY year, mo, da
    LIMIT 35000000
"""

def run_simulator():
    bq_client = bigquery.Client(project=PROJECT_ID)
    
    # Configuración del Publisher
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)
    
    # Opciones de Batch para el cliente de PubSub (optimización de red)
    batch_settings = pubsub_v1.types.BatchSettings(
        max_messages=BATCH_SIZE, 
        max_latency=0.05,
    )
    publisher = pubsub_v1.PublisherClient(batch_settings=batch_settings)

    logging.info(f"Initiating Simulator. Range: 2015-2025. Batch Size: {BATCH_SIZE}")

    # Ejecutando la consulta
    try:
        query_job = bq_client.query(QUERY)
        # Result devuelve un iterador. Page_size ayuda a no cargar todo en RAM de golpe
        rows_iterator = query_job.result(page_size=BATCH_SIZE)
    except Exception as e:
        logging.error(f"Error executing BigQuery query: {e}")
        sys.exit(1)

    batch_rows = [] 
    
    for row in rows_iterator:
        # 1. Convertimos la Row de BigQuery a diccionario
        row_dict = dict(row)
        
        # NOTA: Como vimos antes, 'wdsp' y 'mxpsd' pueden venir como STRING.
        # Si necesitas convertirlos a float para tu pipeline, descomenta esto:
        # if row_dict.get('wdsp') and row_dict['wdsp'] != '999.9':
        #     row_dict['wdsp'] = float(row_dict['wdsp'])
        
        batch_rows.append(row_dict)

        # 2. Cuando el buffer local alcanza el tamaño del lote
        if len(batch_rows) >= BATCH_SIZE:
            start_time = time.time()
            
            # Serializamos la lista de objetos a JSON bytes
            try:
                payload = json.dumps(batch_rows).encode("utf-8")
            except TypeError as e:
                logging.error(f"Serialization error: {e}")
                batch_rows = [] # Limpiar para evitar bucle infinito de errores
                continue

            # Publicamos el mensaje (un solo mensaje contiene el array de datos)
            future = publisher.publish(topic_path, payload)
            
            try:
                future.result() # Esperamos confirmación de publicación
            except Exception as e:
                logging.error(f"PubSub Publish Error: {e}")

            process_time = time.time() - start_time
            
            # Logging informativo
            size_mb = len(payload) / (1024 * 1024)
            logging.info(f"Sent {len(batch_rows)} rows (Years 2015-2025). Time: {process_time:.4f}s. Size: {size_mb:.2f} MB")
            
            # Reiniciamos el buffer
            batch_rows = []

            # Control de flujo (Rate Limiting)
            # Intentamos mantener el ritmo de 1 lote por segundo aprox
            sleep_time = 1.0 - process_time
            if sleep_time > 0:
                time.sleep(sleep_time)

    # Enviar cualquier remanente que haya quedado en el buffer al final del loop
    if batch_rows:
        payload = json.dumps(batch_rows).encode("utf-8")
        future = publisher.publish(topic_path, payload)
        future.result()
        logging.info(f"Sent final {len(batch_rows)} rows.")

if __name__ == "__main__":
    try:
        run_simulator()
    except KeyboardInterrupt:
        logging.info("Simulation Interrupted by User")
    except Exception as e:
        logging.error(f"Critical Error: {e}")
        sys.exit(1)
