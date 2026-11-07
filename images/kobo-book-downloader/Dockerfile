FROM python:3.15-alpine3.24@sha256:a0f2e268f2ce8f3709611a90054ec6a8ae31f6251f5896b8648b9bb99835e30a AS builder

WORKDIR /app
RUN apk add --no-cache git
# Upstream publishes no releases or tags, so pin the branch SHA rather than
# rebuild whatever HEAD happens to be. Renovate moves it via git-refs.
ARG KOBO_REF=37af286854fd9846a05193959e06c51388b46a3e
RUN git clone https://github.com/TnS-hun/kobo-book-downloader.git . \
    && git checkout "$KOBO_REF"
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

FROM python:3.15-alpine3.24@sha256:a0f2e268f2ce8f3709611a90054ec6a8ae31f6251f5896b8648b9bb99835e30a

WORKDIR /app
COPY --from=builder /app /app
COPY --from=builder /app/deps /app/deps
ENV PYTHONPATH=/app/deps
ENTRYPOINT ["python", "kobo-book-downloader"]
CMD ["--help"]
