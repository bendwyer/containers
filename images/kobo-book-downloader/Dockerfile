FROM python:3.14-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc AS builder

WORKDIR /app
RUN apk add --no-cache git
# Upstream publishes no releases or tags, so pin the branch SHA rather than
# rebuild whatever HEAD happens to be. Renovate moves it via git-refs.
ARG KOBO_REF=745e3975d05a300765243ef54e7979470da7d049
RUN git clone https://github.com/TnS-hun/kobo-book-downloader.git . \
    && git checkout "$KOBO_REF"
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

FROM python:3.14-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc

WORKDIR /app
COPY --from=builder /app /app
COPY --from=builder /app/deps /app/deps
ENV PYTHONPATH=/app/deps
ENTRYPOINT ["python", "kobo-book-downloader"]
CMD ["--help"]
