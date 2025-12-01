import time
import json
import logging
import os
import sys
from google.cloud import bigquery
from google.cloud import pubsub_v1

# Enviroment Variable Configurations
PROJECT_ID = os.getenv("PROJECT_ID")
TOPIC_ID = os.getenv("TOPIC_ID")
BATCH_SIZE = int(os.getenv("BATCH_SIZE")) 

# Log Configuration
logging.basicConfig(stream=sys.stdout, level=logging.INFO, 
                    format='%(asctime)s - [Streaming Simulator] - %(message)s')

# Querying into Big Query (Ordering by date is added to more acurrately simulate real time data generation)
QUERY = """
    SELECT 
        year, mo, da, 
        temp, dewp, slp, stp, visib, wdsp, mxpsd, gust, max, min, prcp, sndp, thunder
    FROM `bigquery-public-data.noaa_gsod.gsod2023`
    ORDER BY year, mo, da
    LIMIT 35000000
"""

def run_simulator():
    bq_client = bigquery.Client(project=PROJECT_ID)
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)

    logging.info(f"Initiating Simulator. Target: {BATCH_SIZE} msg/seg.")

    # Getting the Batches of the Query
    query_job = bq_client.query(QUERY)
    rows_iterator = query_job.result(page_size=BATCH_SIZE)

    batch_buffer = []
    
    # BatchSettings For Pub-Sub
    batch_settings = pubsub_v1.types.BatchSettings(
        max_messages=BATCH_SIZE, 
        max_latency=0.05,
    )
    publisher = pubsub_v1.PublisherClient()
    
    # Buffer de objetos (dicts), no de bytes
    batch_rows = [] 
    
    for row in rows_iterator:
        # 1. Acumulamos el diccionario puro
        batch_rows.append(dict(row))

        # 2. Cuando llegamos a 10,000
        if len(batch_rows) >= BATCH_SIZE:
            start_time = time.time()
            
            payload = json.dumps(batch_rows).encode("utf-8")
            
            # Publishing Per Batch
            future = publisher.publish(topic_path, payload)
            future.result() # Waiting for result
            
            process_time = time.time() - start_time
            
            logging.info(f"{len(batch_rows)} rows sent in {process_time:.4f}s. Aprox. Size: {len(payload)/1024/1024:.2f} MB")
            batch_rows = []

            # Time Control
            sleep_time = 1.0 - process_time
            if sleep_time > 0:
                time.sleep(sleep_time)

if __name__ == "__main__":
    try:
        run_simulator()
    except KeyboardInterrupt:
        logging.info("Simulation Interrupted by User")
    except Exception as e:
        logging.error(f"Critical Error: {e}")
        sys.exit(1)