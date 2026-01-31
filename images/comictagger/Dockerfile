FROM python:3.12-slim-trixie AS builder
RUN apt-get update && apt-get install -y --no-install-recommends git pkg-config libicu-dev g++
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends libicu76 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
ENTRYPOINT ["comictagger"]
CMD ["--help"]
