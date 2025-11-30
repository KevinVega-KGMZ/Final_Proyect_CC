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
        stn, wban, year, mo, da, 
        temp, dewp, slp, stp, visib, wdsp, mxpsd, gust, max, min, prcp
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
    publisher = pubsub_v1.PublisherClient(batch_settings=batch_settings)

    for row in rows_iterator:
        data_str = json.dumps(dict(row)).encode("utf-8")
        batch_buffer.append(data_str)

        if len(batch_buffer) >= BATCH_SIZE:
            start_time = time.time()
            
            # Publication Loop
            for data in batch_buffer:
                publisher.publish(topic_path, data)
            
            process_time = time.time() - start_time
            
            # Cleaning Buffer
            logging.info(f"Batch de {len(batch_buffer)} enviado en {process_time:.4f}s")
            batch_buffer = []

            # Controrlling the publications so that they are constant 10,000/sec
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