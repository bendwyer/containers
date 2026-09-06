FROM python:3.12-alpine3.23 AS builder
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

FROM python:3.12-alpine3.23
RUN apk add --no-cache icu-libs
COPY --from=builder /install /usr/local
ENTRYPOINT ["comictagger"]
CMD ["--help"]
