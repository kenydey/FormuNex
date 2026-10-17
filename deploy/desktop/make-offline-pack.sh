#!/usr/bin/env bash
# Offline image pack for the desktop app (plan §2.2): every image of docker-compose.desktop.yml in one
# gzip'd `docker save` archive, imported by the first-run wizard ("导入离线镜像包") with `docker load`.
#
#   deploy/desktop/make-offline-pack.sh <version> [out_dir]
#   FORMUNEX_IMAGE overrides the backend image (default ghcr.io/kenydey/formunex-backend).
set -euo pipefail
version="${1:?usage: make-offline-pack.sh <version> [out_dir]}"
out="${2:-.}"
image="${FORMUNEX_IMAGE:-ghcr.io/kenydey/formunex-backend}"
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$out"

images=$(FORMUNEX_IMAGE="$image" FORMUNEX_VERSION="$version" FORMUNEX_PG_PASSWORD=x FORMUNEX_NEO4J_PASSWORD=x \
  FORMUNEX_AUTH_JWT_SECRET=x docker compose -f "$here/docker-compose.desktop.yml" config --images | sort -u)
echo "images:"; echo "$images"
for i in $images; do docker pull "$i"; done
file="$out/formunex-images-$version.tar.gz"
# shellcheck disable=SC2086
docker save $images | gzip -1 > "$file"
( cd "$out" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256" )
ls -lh "$out"
