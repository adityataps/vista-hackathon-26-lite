FROM public.ecr.aws/docker/library/python:3.12.0-slim-bullseye
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.0.1 /lambda-adapter /opt/extensions/lambda-adapter

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jobs/pacs008-generator/pacs008_generator/ pacs008_generator/
COPY jobs/pacs008-generator/error_catalog.yaml .
COPY backend/ .
COPY infra/assets/ infra/assets/

EXPOSE 8080
CMD ["sh", "-c", "python seed_db.py && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
