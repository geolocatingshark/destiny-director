#!/bin/sh
# If RAILWAY_SERVICE_NAME is beacon, then start beacon,
# otherwise if RAILWAY_SERVICE_NAME is anchor start anchor
# otherwise raise an error
if [ "$RAILWAY_SERVICE_NAME" = "beacon" ]; then
  atlas migrate apply -u ${MYSQL_URL} && python -OO -m dd.beacon
elif [ "$RAILWAY_SERVICE_NAME" = "anchor" ]; then
    atlas migrate apply -u ${MYSQL_URL} && python -OO -m dd.anchor
  else
    echo "Unknown service name: $RAILWAY_SERVICE_NAME"
    exit 1
fi
