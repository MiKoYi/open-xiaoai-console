#!/usr/bin/env bash
set -e

MODEL=${MODEL:?MODEL is required}
IN_FILE=${IN_FILE:?IN_FILE is required}
OUT_FILE=${OUT_FILE:?OUT_FILE is required}
SSH_PASSWORD=${SSH_PASSWORD:-open-xiaoai}

mkdir -p /out /work

INFO=$(file "/in/$IN_FILE")
COMPRESSION=$(printf '%s' "$INFO" | grep -oE 'xz|gzip|lzo|lz4|zstd' | head -n1)
BLOCKSIZE=$(printf '%s' "$INFO" | grep -oE 'blocksize: [0-9]+' | grep -oE '[0-9]+' | head -n1)
[ -n "$COMPRESSION" ] || COMPRESSION=xz
[ -n "$BLOCKSIZE" ] || BLOCKSIZE=131072

rm -rf /work/rootfs
unsquashfs -d /work/rootfs "/in/$IN_FILE" >/dev/null

PASSWORD=$(openssl passwd -1 -salt "open-xiaoai" "$SSH_PASSWORD")

apply_patches() {
  local dir="$1"
  [ -d "$dir" ] || return 0
  for file in "$dir"/*; do
    [ -f "$file" ] || continue
    case "$file" in
      *.patch)
        sed 's/\r$//' "$file" | sed "s|{SSH_PASSWORD}|$PASSWORD|g" > /tmp/current.patch
        patch -p1 < /tmp/current.patch
        ;;
      *.sh)
        tr -d '\r' < "$file" > /tmp/current.sh
        bash /tmp/current.sh
        ;;
    esac
  done
}

cd /work/rootfs
apply_patches /patches
apply_patches "/patches/$MODEL"

rm -f "/out/$OUT_FILE"
mksquashfs /work/rootfs "/out/$OUT_FILE" \
  -comp "$COMPRESSION" -b "$BLOCKSIZE" \
  -noappend -all-root -always-use-fragments -no-xattrs -no-exports >/dev/null

SIZE=$(wc -c < "/out/$OUT_FILE")
MAX=$((0x02800000))
if [ "$SIZE" -ge "$MAX" ]; then
  echo "firmware too large: $SIZE >= $MAX" >&2
  exit 1
fi

echo "PATCH_OK size=$SIZE comp=$COMPRESSION block=$BLOCKSIZE"
