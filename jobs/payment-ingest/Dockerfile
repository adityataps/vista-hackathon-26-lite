FROM amazon/aws-lambda-python:3.12

COPY requirements.txt ./
RUN pip install -r requirements.txt --no-cache-dir

COPY handler.py error_rules.py reference_data.py notifier.py ./

CMD ["handler.lambda_handler"]
