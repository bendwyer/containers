FROM python:3.15-alpine3.24@sha256:20a82dbc2783112ddb91d1534603f802d222a3e63e593cd002eb2ac534e70211 AS builder
RUN apk add --no-cache icu-dev g++ pkgconf
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.15-alpine3.24@sha256:20a82dbc2783112ddb91d1534603f802d222a3e63e593cd002eb2ac534e70211
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
