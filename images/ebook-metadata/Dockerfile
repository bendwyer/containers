FROM python:3.14-alpine3.24@sha256:01ddf82b43479870a7ea87037b7d68472bef12cc244941843c26bb75bf9a9b04 AS builder
RUN apk add --no-cache gcc musl-dev libxml2-dev libxslt-dev
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-alpine3.24@sha256:01ddf82b43479870a7ea87037b7d68472bef12cc244941843c26bb75bf9a9b04
RUN apk add --no-cache libxml2 libxslt
COPY --from=builder /install /usr/local
ENV PYTHONUNBUFFERED=1
COPY ebook_metadata.py /app/ebook_metadata.py
ENTRYPOINT ["python3", "/app/ebook_metadata.py"]
CMD ["--help"]
