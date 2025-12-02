import time
import json
import logging
import os
import sys
from google.cloud import bigquery
from google.cloud import pubsub_v1

# Configuracion
PROJECT_ID = os.getenv("PROJECT_ID")
TOPIC_ID = os.getenv("TOPIC_ID")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 5000)) 

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(asctime)s - [Generator] - %(message)s')

# Consulta Multianual (2015-2025)
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
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)

    logging.info(f"Iniciando Simulador. Rango: 2015-2025. Batch Size: {BATCH_SIZE}")

    try:
        query_job = bq_client.query(QUERY)
        rows_iterator = query_job.result(page_size=BATCH_SIZE)
    except Exception as e:
        logging.error(f"Error ejecutando BigQuery: {e}")
        sys.exit(1)

    batch_rows = [] 
    
    for row in rows_iterator:
        row_dict = dict(row)
        batch_rows.append(row_dict)

        if len(batch_rows) >= BATCH_SIZE:
            start_time = time.time()
            try:
                # Serializar dates y numericos a string/json standard
                payload = json.dumps(batch_rows, default=str).encode("utf-8")
                future = publisher.publish(topic_path, payload)
                future.result() 
            except Exception as e:
                logging.error(f"Error publicando: {e}")
                batch_rows = []
                continue

            process_time = time.time() - start_time
            size_mb = len(payload) / (1024 * 1024)
            logging.info(f"Enviadas {len(batch_rows)} filas. Tiempo: {process_time:.4f}s. Tamano: {size_mb:.2f} MB")
            
            batch_rows = []
            sleep_time = 1.0 - process_time
            if sleep_time > 0:
                time.sleep(sleep_time)

    if batch_rows:
        payload = json.dumps(batch_rows, default=str).encode("utf-8")
        publisher.publish(topic_path, payload).result()
        logging.info(f"Enviado lote final de {len(batch_rows)} filas.")

if __name__ == "__main__":
    run_simulator()
