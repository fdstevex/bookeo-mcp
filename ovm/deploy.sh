#!/bin/bash
# Deploy script run on the Oracle VM. Installed at ~/apps/bookeo/deploy.sh and
# wired as the forced command for the CI deploy key in
# ~/.ssh/authorized_keys, so the key can only ever run this script. CI passes
# the image tag (git sha) as the SSH command; it arrives here in
# SSH_ORIGINAL_COMMAND.
set -euo pipefail
cd "${BOOKEO_APP_DIR:-/home/ubuntu/apps/bookeo}"

TAG="${SSH_ORIGINAL_COMMAND:-${1:-latest}}"
case "$TAG" in
  *[!A-Za-z0-9._-]*|"") echo "refusing bad tag: $TAG" >&2; exit 1 ;;
esac

if grep -q '^IMAGE_TAG=' .env; then
  sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/" .env
else
  echo "IMAGE_TAG=$TAG" >> .env
fi

IMAGE="ghcr.io/fdstevex/bookeo-mcp:$TAG"
echo "Deploying $IMAGE"
docker pull --quiet "$IMAGE"

# The compose file ships inside the image, so CI deploys it along with the
# code while the deploy key still carries nothing but a tag. Images from before
# that, or a file compose rejects, leave the current one in place.
if docker run --rm --entrypoint cat "$IMAGE" /app/ovm/docker-compose.yml \
     > docker-compose.yml.new 2>/dev/null \
   && [ -s docker-compose.yml.new ] \
   && docker compose -f docker-compose.yml.new config --quiet; then
  if cmp -s docker-compose.yml.new docker-compose.yml; then
    rm docker-compose.yml.new
  else
    echo "Updating docker-compose.yml from the image"
    [ -f docker-compose.yml ] && cp docker-compose.yml docker-compose.yml.prev
    mv docker-compose.yml.new docker-compose.yml
  fi
else
  echo "No usable compose file in $IMAGE; keeping the current one" >&2
  rm -f docker-compose.yml.new
fi

docker compose up -d --remove-orphans
docker image prune -f >/dev/null
docker compose ps
