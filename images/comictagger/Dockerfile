FROM python:3.14-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc AS builder
RUN apk add --no-cache icu-dev g++ pkgconf patch
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
# Apply local patches to installed site-packages. See patches/README.md.
COPY patches/ /patches/
RUN set -eu; \
    site=$(echo /install/lib/python*/site-packages); \
    [ -d "$site" ] || { echo "site-packages not found: $site" >&2; exit 1; }; \
    for p in /patches/*.patch; do \
        echo "Applying $p"; \
        patch -p1 --directory="$site" < "$p"; \
    done

FROM python:3.14-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc
RUN apk add --no-cache icu-libs
COPY --from=builder /install /usr/local
ENTRYPOINT ["comictagger"]
CMD ["--help"]
