FROM python:3.12-alpine3.23 AS builder

WORKDIR /app
RUN apk add --no-cache git
RUN git clone https://github.com/TnS-hun/kobo-book-downloader.git .
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

FROM python:3.12-alpine3.23

WORKDIR /app
COPY --from=builder /app /app
COPY --from=builder /app/deps /app/deps
ENV PYTHONPATH=/app/deps
ENTRYPOINT ["python", "kobo-book-downloader"]
CMD ["--help"]
