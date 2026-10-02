FROM python:3.14-alpine3.24@sha256:2e740b2c28a426e74f11396c05e38afb3191acced75045b8d62df573c1dc8ce8 AS builder
RUN apk add --no-cache gcc musl-dev libxml2-dev libxslt-dev
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-alpine3.24@sha256:2e740b2c28a426e74f11396c05e38afb3191acced75045b8d62df573c1dc8ce8
RUN apk add --no-cache libxml2 libxslt
COPY --from=builder /install /usr/local
ENV PYTHONUNBUFFERED=1
COPY ebook_metadata.py /app/ebook_metadata.py
ENTRYPOINT ["python3", "/app/ebook_metadata.py"]
CMD ["--help"]
