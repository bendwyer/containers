FROM python:3.14-alpine3.24@sha256:2e740b2c28a426e74f11396c05e38afb3191acced75045b8d62df573c1dc8ce8 AS builder

WORKDIR /app
RUN apk add --no-cache git
# Upstream publishes no releases or tags, so pin the branch SHA rather than
# rebuild whatever HEAD happens to be. Renovate moves it via git-refs.
ARG KOBO_REF=37af286854fd9846a05193959e06c51388b46a3e
RUN git clone https://github.com/TnS-hun/kobo-book-downloader.git . \
    && git checkout "$KOBO_REF"
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

FROM python:3.14-alpine3.24@sha256:2e740b2c28a426e74f11396c05e38afb3191acced75045b8d62df573c1dc8ce8

WORKDIR /app
COPY --from=builder /app /app
COPY --from=builder /app/deps /app/deps
ENV PYTHONPATH=/app/deps
ENTRYPOINT ["python", "kobo-book-downloader"]
CMD ["--help"]
