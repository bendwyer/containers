FROM python:3.15-alpine3.24@sha256:a0f2e268f2ce8f3709611a90054ec6a8ae31f6251f5896b8648b9bb99835e30a AS builder
RUN apk add --no-cache gcc musl-dev libxml2-dev libxslt-dev
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.15-alpine3.24@sha256:a0f2e268f2ce8f3709611a90054ec6a8ae31f6251f5896b8648b9bb99835e30a
RUN apk add --no-cache libxml2 libxslt
COPY --from=builder /install /usr/local
ENV PYTHONUNBUFFERED=1
COPY ebook_metadata.py /app/ebook_metadata.py
ENTRYPOINT ["python3", "/app/ebook_metadata.py"]
CMD ["--help"]
