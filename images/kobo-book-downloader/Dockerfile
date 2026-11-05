FROM python:3.14-alpine3.24@sha256:b15fd707501f872b2d46a8edacb18aca52db393460d17a60a20e7231dbdb3263 AS builder

WORKDIR /app
RUN apk add --no-cache git
# Upstream publishes no releases or tags, so pin the branch SHA rather than
# rebuild whatever HEAD happens to be. Renovate moves it via git-refs.
ARG KOBO_REF=37af286854fd9846a05193959e06c51388b46a3e
RUN git clone https://github.com/TnS-hun/kobo-book-downloader.git . \
    && git checkout "$KOBO_REF"
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

FROM python:3.14-alpine3.24@sha256:b15fd707501f872b2d46a8edacb18aca52db393460d17a60a20e7231dbdb3263

WORKDIR /app
COPY --from=builder /app /app
COPY --from=builder /app/deps /app/deps
ENV PYTHONPATH=/app/deps
ENTRYPOINT ["python", "kobo-book-downloader"]
CMD ["--help"]
