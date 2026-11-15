# Upstream's Docker image cannot start c2e from v12.1.1 on (it never installs
# PySide6) and sits on Debian 11. Its Linux AppImage is the build upstream
# packages and tests each release, with Python and every dependency bundled,
# so this image unpacks that instead of rebuilding it.
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS fetch
ARG VERSION
RUN apk add --no-cache curl
RUN curl -fsSL --retry 3 -o /tmp/kcc.AppImage \
      "https://github.com/ciromattia/kcc/releases/download/v${VERSION}/kcc_linux-${VERSION}-$(uname -m).AppImage" && \
    chmod +x /tmp/kcc.AppImage && \
    cd /tmp && ./kcc.AppImage --appimage-extract >/dev/null && \
    mv /tmp/AppDir /opt/kcc
# The AppImage ships no bytecode, and uid 1000 cannot write it at run time.
RUN /opt/kcc/bin/python3 -m compileall -q /opt/kcc/lib

FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
# kcc shells out to 7z for archive inputs; the AppImage does not bundle it.
RUN apk add --no-cache 7zip
COPY --from=fetch /opt/kcc /opt/kcc
COPY c2e /usr/local/bin/c2e
USER 1000:1000
# Upstream has no tests, and shipped c2e unable to start for three releases.
# Convert a PDF so a release that breaks conversion fails the build.
RUN set -eu; \
    work=$(mktemp -d); \
    /opt/kcc/bin/python3 -c "import pymupdf, sys; d = pymupdf.open(); \
[d.new_page(width=600, height=900).insert_text((50, 100), f'Page {i + 1}', fontsize=40) for i in range(3)]; \
d.save(sys.argv[1])" "$work/Smoke Test 1.pdf"; \
    c2e -n -f CBZ -o "$work/out" "$work/Smoke Test 1.pdf" >/dev/null; \
    /opt/kcc/bin/python3 -c "import glob, sys, zipfile; z = glob.glob(sys.argv[1] + '/*.cbz'); \
assert len(z) == 1, z; n = len(zipfile.ZipFile(z[0]).namelist()); \
assert n == 3, f'{n} pages in CBZ, expected 3'" "$work/out"; \
    rm -rf "$work"
ENTRYPOINT ["c2e"]
CMD ["--help"]
