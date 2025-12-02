FROM python:3.9-slim
ENV PYTHONUNBUFFERED=1
RUN pip install google-cloud-bigquery google-cloud-pubsub
COPY main.py .
CMD ["python", "main.py"]
