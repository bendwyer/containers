FROM python:3.12-alpine AS builder
RUN apk add --no-cache gcc musl-dev libxml2-dev libxslt-dev
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-alpine
RUN apk add --no-cache libxml2 libxslt
COPY --from=builder /install /usr/local
ENV PYTHONUNBUFFERED=1
COPY ebook_metadata.py /app/ebook_metadata.py
ENTRYPOINT ["python3", "/app/ebook_metadata.py"]
CMD ["--help"]
