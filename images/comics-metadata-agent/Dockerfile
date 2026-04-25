FROM python:3.12-alpine3.23 AS builder
RUN apk add --no-cache gcc g++ musl-dev pkgconf \
    jpeg-dev zlib-dev libpng-dev libwebp-dev icu-dev
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-alpine3.23
RUN apk add --no-cache jpeg zlib libpng libwebp icu-libs
COPY --from=builder /install /usr/local
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
COPY agent.py agent_runner.py apply.py bundle_planner.py comicvine_client.py \
     cover_extractor.py decision_log.py filename_parser.py kavita_client.py \
     prompt.py replan.py series_grouper.py /app/
ENTRYPOINT ["python", "-B", "/app/agent.py"]
CMD ["--help"]
