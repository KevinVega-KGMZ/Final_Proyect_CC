import argparse
import json
import logging
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions, StandardOptions, GoogleCloudOptions

# --- Map of Not Available Values ---
SENTINELS = {
    'temp': 9999.9,  'dewp': 9999.9,  'slp': 9999.9,   'stp': 9999.9,
    'visib': 999.9,  'wdsp': 999.9,   'mxpsd': 999.9,  'gust': 999.9,
    'max': 9999.9,   'min': 9999.9,   'prcp': 99.99,   'sndp': 999.9
}

class CleanBatch(beam.DoFn):

    def process(self, element):
        try:
            # List Decoding
            input_batch = json.loads(element.decode('utf-8'))
            output_batch = []
            
            # --- LOG DE ENTRADA (Opcional, para debug) ---
            # Calcula cuantas filas vienen en este mensaje
            total_input = len(input_batch)

            for row in input_batch:
                clean_row = {}
                is_valid_row = True

                # Data Cleaning
                for key, value in row.items():
                    
                    # Convert to Float (If Possible)
                    try:
                        float_val = float(value)
                    except (ValueError, TypeError):
                        float_val = value

                    # Check if Value is Not Available
                    if key in SENTINELS and float_val == SENTINELS[key]:
                        is_valid_row = False
                    elif key == "thunder" and float_val==None:
                        float_val=0
                        clean_row[key] = float_val
                    else:
                        # List with Clean Rows
                        clean_row[key] = float_val

                # Adding Only Clean Rows
                if is_valid_row:
                    output_batch.append(clean_row)

            # --- LOG DE SALIDA ---
            # Muestra cuantas entraron vs cuantas quedaron
            total_output = len(output_batch)
            if total_output > 0:
                logging.info(f"OK: Batch Processed: Input {total_input} rows -> Output {total_output} clean rows.")
            else:
                logging.warning(f"WARNING: Batch Processed: Input {total_input} rows -> ALL FILTERED OUT (0 output).")

            # Sending the Batch
            if output_batch:
                yield json.dumps(output_batch).encode('utf-8')

        except Exception as e:
            logging.error(f"ERROR: Error Processing the Batch: {e}")

# --- PIPELINE ---
def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project_id', required=True)
    parser.add_argument('--input_topic', required=True)
    parser.add_argument('--output_topic', required=True)
    parser.add_argument('--staging_bucket', required=True)
    
    # Argumentos opcionales para controlar workers desde consola si se desea
    parser.add_argument('--disk_size_gb', type=int, default=30)
    
    known_args, pipeline_args = parser.parse_known_args()

    options = PipelineOptions(pipeline_args)
    options.view_as(StandardOptions).streaming = True 
    options.view_as(StandardOptions).runner = 'DataflowRunner'
    
    google_cloud_options = options.view_as(GoogleCloudOptions)
    google_cloud_options.project = known_args.project_id
    google_cloud_options.region = 'us-central1'
    google_cloud_options.job_name = 'noaa-batch-cleaner'
    google_cloud_options.staging_location = f"{known_args.staging_bucket}/staging"
    google_cloud_options.temp_location = f"{known_args.staging_bucket}/temp"

    with beam.Pipeline(options=options) as p:
        (
            p
            | 'Read PubSub Batch' >> beam.io.ReadFromPubSub(topic=known_args.input_topic)
            | 'Cleaning Batch' >> beam.ParDo(CleanBatch())
            | 'Publishing Clean Batch' >> beam.io.WriteToPubSub(topic=known_args.output_topic)
        )

if __name__ == '__main__':
    # Configurar nivel de log general
    logging.getLogger().setLevel(logging.INFO)

    # --- SILENCIAR WARNINGS DE GOOGLE ---
    # Esto elimina el ruido de "httplib2 transport does not support per-request timeout"
    logging.getLogger("google_auth_httplib2").setLevel(logging.ERROR)
    logging.getLogger("google.auth.transport.requests").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    
    run()
