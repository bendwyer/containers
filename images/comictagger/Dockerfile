FROM python:3.12-alpine3.23 AS builder
RUN apk add --no-cache icu-dev g++ pkgconf
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-alpine3.23
RUN apk add --no-cache icu-libs
COPY --from=builder /install /usr/local
ENTRYPOINT ["comictagger"]
CMD ["--help"]
