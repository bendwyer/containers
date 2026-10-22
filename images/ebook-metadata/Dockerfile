FROM python:3.14-alpine3.24@sha256:601e99e090cb08c2784a42395487709500bc39930307fe72f3f11a8f3c430762 AS builder
RUN apk add --no-cache gcc musl-dev libxml2-dev libxslt-dev
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-alpine3.24@sha256:601e99e090cb08c2784a42395487709500bc39930307fe72f3f11a8f3c430762
RUN apk add --no-cache libxml2 libxslt
COPY --from=builder /install /usr/local
ENV PYTHONUNBUFFERED=1
COPY ebook_metadata.py /app/ebook_metadata.py
ENTRYPOINT ["python3", "/app/ebook_metadata.py"]
CMD ["--help"]
