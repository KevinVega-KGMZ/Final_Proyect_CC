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

# Grupos de tratamiento especial
ZERO_REPLACE_COLS = ['sndp', 'prcp']
MEAN_REPLACE_COLS = ['slp']

class CleanBatch(beam.DoFn):

    def process(self, element):
        try:
            # 1. Deserializar el batch
            input_batch = json.loads(element.decode('utf-8'))
            output_batch = []
            
            total_input = len(input_batch)

            # 2. Pre-cálculo para la Media (SLP)
            # Extraemos todos los valores válidos de 'slp' en este batch
            valid_slp_values = []
            for row in input_batch:
                try:
                    val = float(row.get('slp', SENTINELS['slp']))
                    # Solo lo consideramos si NO es el valor centinela
                    if val != SENTINELS['slp']:
                        valid_slp_values.append(val)
                except (ValueError, TypeError):
                    pass
            
            # Calculamos la media del batch. Si no hay ningún valor válido, queda None.
            batch_slp_mean = sum(valid_slp_values) / len(valid_slp_values) if valid_slp_values else None

            # 3. Procesamiento fila por fila
            for row in input_batch:
                clean_row = {}
                should_drop_row = False

                for key, value in row.items():
                    # Si ya decidimos borrar la fila por una columna anterior, paramos.
                    if should_drop_row:
                        break

                    # Intentar convertir a float
                    try:
                        f_val = float(value)
                    except (ValueError, TypeError):
                        f_val = value # Se mantiene como string (ej. fecha, ids)

                    # --- LÓGICA DE LIMPIEZA ---

                    # CASO A: Ir a 0 (sndp, prcp)
                    if key in ZERO_REPLACE_COLS:
                        if f_val == SENTINELS[key]:
                            clean_row[key] = 0.0
                        else:
                            clean_row[key] = f_val
                    
                    # CASO B: Ir a la Media del Batch (slp)
                    elif key in MEAN_REPLACE_COLS:
                        if f_val == SENTINELS[key]:
                            if batch_slp_mean is not None:
                                clean_row[key] = batch_slp_mean
                            else:
                                # Si todo el batch vino con 9999.9, no podemos calcular media.
                                # Decisión: Eliminar la fila (es dato corrupto irrecuperable).
                                should_drop_row = True
                        else:
                            clean_row[key] = f_val

                    # CASO C: El resto de sentinels -> DROP ROW
                    elif key in SENTINELS:
                        if f_val == SENTINELS[key]:
                            should_drop_row = True # Marcamos para eliminar
                        else:
                            clean_row[key] = f_val
                    
                    # CASO D: Lógica especial Thunder (None -> 0)
                    elif key == "thunder":
                        if f_val is None:
                            clean_row[key] = 0.0
                        else:
                            clean_row[key] = f_val
                    
                    # CASO E: Columnas normales (year, mo, da, stn, etc.)
                    else:
                        clean_row[key] = f_val

                # Solo agregamos si sobrevivió a los filtros
                if not should_drop_row:
                    output_batch.append(clean_row)

            # --- LOG DE SALIDA ---
            total_output = len(output_batch)
            if total_output > 0:
                logging.info(f"Batch Processed. Mean SLP: {batch_slp_mean if batch_slp_mean else 'N/A':.2f}. Input: {total_input} -> Output: {total_output}")
            else:
                logging.warning(f"Batch Processed. Input: {total_input} -> ALL DROPPED.")

            # Enviar batch limpio
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
    
    parser.add_argument('--disk_size_gb', type=int, default=30)
    
    known_args, pipeline_args = parser.parse_known_args()

    options = PipelineOptions(pipeline_args)
    options.view_as(StandardOptions).streaming = True 
    options.view_as(StandardOptions).runner = 'DataflowRunner'
    
    google_cloud_options = options.view_as(GoogleCloudOptions)
    google_cloud_options.project = known_args.project_id
    google_cloud_options.region = 'us-central1'
    google_cloud_options.job_name = 'noaa-batch-cleaner-v2' # Cambié el nombre para diferenciarlo
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
    logging.getLogger().setLevel(logging.INFO)
    
    # Reducción de ruido en logs
    logging.getLogger("google_auth_httplib2").setLevel(logging.ERROR)
    logging.getLogger("google.auth.transport.requests").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    
    run()
