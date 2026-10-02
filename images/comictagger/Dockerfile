FROM python:3.14-alpine3.24@sha256:2e740b2c28a426e74f11396c05e38afb3191acced75045b8d62df573c1dc8ce8 AS builder
RUN apk add --no-cache icu-dev g++ pkgconf
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-alpine3.24@sha256:2e740b2c28a426e74f11396c05e38afb3191acced75045b8d62df573c1dc8ce8
RUN apk add --no-cache icu-libs
COPY --from=builder /install /usr/local
# Nothing declares a supported comictagger range for the talker, and nothing
# in the build fails when its API host dies, so assert both on every build.
RUN set -eu; \
    comictagger --list-plugins | grep -q '^mangabaka '; \
    python -c "import pathlib, tempfile; \
from mangabaka_talker.mangabaka import MangaBakaTalker as T; \
t = T('smoke', pathlib.Path(tempfile.mkdtemp())); \
msg, ok = t.check_status({f'{t.id}_url': ''}); \
assert ok, f'{t.default_api_url}: {msg}'"
ENTRYPOINT ["comictagger"]
CMD ["--help"]
