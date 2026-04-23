FROM python:3.12-alpine3.23 AS builder
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-alpine3.23
COPY --from=builder /install /usr/local
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
COPY agent.py agent_runner.py comicvine_client.py cover_extractor.py \
     decision_log.py filename_parser.py kavita_client.py prompt.py \
     series_grouper.py /app/
ENTRYPOINT ["python", "-B", "/app/agent.py"]
CMD ["--help"]
