ARG OWNER=barryw
ARG REPO=PaperlessMCP

# Upstream ships no release binaries and only a linux/amd64 image, so the
# tagged source is fetched and built here instead of pulling a binary.
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS fetch

ARG OWNER
ARG REPO
ARG VERSION

RUN apk add --no-cache curl tar && \
  mkdir -p /src && \
  curl -sSL "https://github.com/${OWNER}/${REPO}/archive/refs/tags/v${VERSION}.tar.gz" \
    | tar xzf - --strip-components=1 -C /src && \
  printf '%s' "${VERSION}" > /src/.version

FROM mcr.microsoft.com/dotnet/sdk:10.0@sha256:8c6edfb26c85153f236d26efebd9ad349aec403583f343f201ee67838b23658d AS build

WORKDIR /src
COPY --from=fetch /src .

# Directory.Build.props reads the version from /p:Version and otherwise stamps
# the assembly 0.0.0-local. Framework-dependent publish, so the output is
# portable IL and needs no per-architecture handling.
RUN dotnet publish PaperlessMCP/PaperlessMCP.csproj \
  -c Release -o /app/publish "/p:Version=$(cat .version)"

# Microsoft's chiseled base is the distroless equivalent for .NET: no shell, no
# package manager, non-root by default. distroless/static cannot be used because
# a framework-dependent build needs the runtime present.
FROM mcr.microsoft.com/dotnet/aspnet:10.0-noble-chiseled@sha256:bcdfe6b8e923ad3d05f01dfa805ab97c99d742823ce030856002a6db38122712

WORKDIR /app
COPY --from=build /app/publish .

ENV ASPNETCORE_URLS=http://+:5000
ENV MCP_PORT=5000

EXPOSE 5000
ENTRYPOINT ["dotnet", "PaperlessMCP.dll"]
