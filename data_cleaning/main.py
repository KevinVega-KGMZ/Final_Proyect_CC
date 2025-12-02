import argparse
import json
import logging
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions, StandardOptions, GoogleCloudOptions

# Mapa de valores centinela (SIN gust NI visib)
SENTINELS = {
    'temp': 9999.9,  'dewp': 9999.9,  'slp': 9999.9,   'stp': 9999.9,
    'wdsp': 999.9,   'mxpsd': 999.9,
    'max': 9999.9,   'min': 9999.9,   'prcp': 99.99,   'sndp': 999.9
}

# Reglas de negocio
ZERO_REPLACE_COLS = ['sndp', 'prcp']
MEAN_REPLACE_COLS = ['slp']

class CleanBatch(beam.DoFn):
    def process(self, element):
        try:
            input_batch = json.loads(element.decode('utf-8'))
            output_batch = []
            
            # Calculamos total entrada para el log
            total_input = len(input_batch)
            
            # 1. Calcular media del batch para SLP
            valid_slp = []
            for row in input_batch:
                try:
                    val = float(row.get('slp', SENTINELS['slp']))
                    if val != SENTINELS['slp']:
                        valid_slp.append(val)
                except: pass
            
            batch_slp_mean = sum(valid_slp) / len(valid_slp) if valid_slp else None

            # 2. Procesar filas
            for row in input_batch:
                clean_row = {}
                drop_row = False

                for key, value in row.items():
                    if drop_row: break

                    try: f_val = float(value)
                    except: f_val = value

                    # Regla A: Imputar a 0
                    if key in ZERO_REPLACE_COLS:
                        clean_row[key] = 0.0 if f_val == SENTINELS[key] else f_val
                    
                    # Regla B: Imputar a Media
                    elif key in MEAN_REPLACE_COLS:
                        if f_val == SENTINELS[key]:
                            if batch_slp_mean is not None:
                                clean_row[key] = batch_slp_mean
                            else:
                                drop_row = True # No hay media disponible, descartar
                        else:
                            clean_row[key] = f_val

                    # Regla C: Drop estandar
                    elif key in SENTINELS:
                        if f_val == SENTINELS[key]:
                            drop_row = True
                        else:
                            clean_row[key] = f_val
                    
                    # Regla D: Thunder (None a 0)
                    elif key == "thunder":
                        clean_row[key] = 0.0 if f_val is None else float(f_val)
                    
                    else:
                        clean_row[key] = f_val

                if not drop_row:
                    output_batch.append(clean_row)

            # --- LOGGING (SIN EMOJIS) ---
            total_output = len(output_batch)
            slp_log = f"{batch_slp_mean:.2f}" if batch_slp_mean is not None else "N/A"
            
            if total_output > 0:
                logging.info(f"Batch procesado. Entrada: {total_input} -> Salida: {total_output}. Media SLP: {slp_log}")
            else:
                logging.warning(f"ALERTA: Batch procesado. Entrada: {total_input} -> TODOS FILTRADOS (Salida 0).")

            if output_batch:
                yield json.dumps(output_batch).encode('utf-8')

        except Exception as e:
            logging.error(f"Error procesando batch: {e}")

def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project_id', required=True)
    parser.add_argument('--input_topic', required=True)
    parser.add_argument('--output_topic', required=True)
    parser.add_argument('--staging_bucket', required=True)
    
    known_args, pipeline_args = parser.parse_known_args()

    options = PipelineOptions(pipeline_args)
    options.view_as(StandardOptions).streaming = True 
    options.view_as(StandardOptions).runner = 'DataflowRunner'
    
    google_cloud_options = options.view_as(GoogleCloudOptions)
    google_cloud_options.project = known_args.project_id
    google_cloud_options.region = 'us-central1'
    google_cloud_options.staging_location = f"{known_args.staging_bucket}/staging"
    google_cloud_options.temp_location = f"{known_args.staging_bucket}/temp"

    with beam.Pipeline(options=options) as p:
        (
            p
            | 'Read PubSub' >> beam.io.ReadFromPubSub(topic=known_args.input_topic)
            | 'Clean Logic' >> beam.ParDo(CleanBatch())
            | 'Write PubSub' >> beam.io.WriteToPubSub(topic=known_args.output_topic)
        )

if __name__ == '__main__':
    logging.getLogger().setLevel(logging.INFO)
    run()
