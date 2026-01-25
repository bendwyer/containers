FROM python:3.12-slim AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git \
  && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/TnS-hun/kobo-book-downloader.git .
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

FROM gcr.io/distroless/python3-debian12

WORKDIR /app
COPY --from=builder /app /app
COPY --from=builder /app/deps /app/deps
ENV PYTHONPATH=/app/deps
USER nonroot
ENTRYPOINT ["python", "kobo-book-downloader"]
CMD ["--help"]
