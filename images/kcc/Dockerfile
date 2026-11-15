# Upstream's image is on Debian 11, which can no longer install the PySide6
# that c2e imports. Ubuntu 26.04 has the glibc that PySide6 and PyMuPDF wheels
# need; neither ships a musl wheel, so Alpine would mean compiling MuPDF.
FROM ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96 AS build
ARG VERSION
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates wget python3-venv && \
    rm -rf /var/lib/apt/lists/*
RUN wget -qO /tmp/kcc.tar.gz "https://github.com/ciromattia/kcc/archive/refs/tags/v${VERSION}.tar.gz" && \
    tar -xzf /tmp/kcc.tar.gz -C /opt && \
    mv "/opt/kcc-${VERSION}" /opt/kcc
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: \
      -r /tmp/requirements.txt -r /opt/kcc/requirements-docker.txt

FROM ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96
# libglib2.0 is what PySide6's QtCore loads; 7zip unpacks archive inputs.
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 7zip libglib2.0-0t64 && \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/kcc /opt/kcc
ENV PATH=/opt/venv/bin:$PATH
RUN ln -s /opt/kcc/kcc-c2e.py /usr/local/bin/c2e
USER 1000:1000
# Upstream shipped c2e unable to start for three releases; fail the build instead.
RUN c2e --help >/dev/null
ENTRYPOINT ["c2e"]
CMD ["--help"]
