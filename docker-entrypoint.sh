#!/bin/sh
# jemalloc (installed in the Dockerfile) is preloaded for BEACON ONLY. beacon is a
# long-lived, allocation-churny process (thousands of guilds of gateway cache) where
# jemalloc's background decay returns freed pages to the OS and cut its RSS materially
# (Railway bills memory-over-time). anchor is small and its memory is dominated by the
# manifest sqlite page cache, not churny heap; there jemalloc's per-arena overhead added
# ~70MB of RSS for no benefit (the manifest lazy-load already prevents anchor's per-post
# heap spike), so anchor stays on glibc. Set inside each branch so the choice is explicit.
# atlas is a static Go binary and is unaffected by LD_PRELOAD.
preload_jemalloc() {
  jemalloc="/usr/lib/$(uname -m)-linux-gnu/libjemalloc.so.2"
  if [ -f "$jemalloc" ]; then
    export LD_PRELOAD="$jemalloc"
    export MALLOC_CONF="background_thread:true,dirty_decay_ms:10000,muzzy_decay_ms:10000"
  fi
}

# If RAILWAY_SERVICE_NAME is beacon, then start beacon,
# otherwise if RAILWAY_SERVICE_NAME is anchor start anchor
# otherwise raise an error
if [ "$RAILWAY_SERVICE_NAME" = "beacon" ]; then
  preload_jemalloc
  atlas migrate apply -u ${MYSQL_URL} && python -OO -m dd.beacon
elif [ "$RAILWAY_SERVICE_NAME" = "anchor" ]; then
    atlas migrate apply -u ${MYSQL_URL} && python -OO -m dd.anchor
  else
    echo "Unknown service name: $RAILWAY_SERVICE_NAME"
    exit 1
fi
