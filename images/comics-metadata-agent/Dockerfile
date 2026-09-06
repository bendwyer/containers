FROM python:3.14-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc AS builder
RUN apk add --no-cache gcc g++ musl-dev pkgconf \
    jpeg-dev zlib-dev libpng-dev libwebp-dev icu-dev
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
# The shared workflow runs no test job, so the suite gates the image here. The
# runtime copies /install from this stage, so it cannot be skipped.
WORKDIR /src
COPY . .
RUN set -eu; \
    site=$(echo /install/lib/python*/site-packages); \
    PYTHONPATH="$site" python -m unittest discover -p "test_*.py"

FROM python:3.14-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc
RUN apk add --no-cache jpeg zlib libpng libwebp icu-libs
COPY --from=builder /install /usr/local
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
COPY agent.py agent_runner.py apply.py bundle_planner.py comicvine_client.py \
     cover_extractor.py decision_log.py filename_parser.py kavita_client.py \
     mangabaka_client.py prompt.py replan.py series_grouper.py /app/
# The agent shells out to comictagger, so the same talker check applies here.
# It does not cover mangabaka_client.py, which calls the API directly.
RUN set -eu; \
    comictagger --list-plugins | grep -q '^mangabaka '; \
    python -c "import pathlib, tempfile; \
from mangabaka_talker.mangabaka import MangaBakaTalker as T; \
t = T('smoke', pathlib.Path(tempfile.mkdtemp())); \
msg, ok = t.check_status({f'{t.id}_url': ''}); \
assert ok, f'{t.default_api_url}: {msg}'"
ENTRYPOINT ["python", "-B", "/app/agent.py"]
CMD ["--help"]
