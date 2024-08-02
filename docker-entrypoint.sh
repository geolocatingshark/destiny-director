#!/bin/sh
source /venv/bin/activate
# If RAILWAY_SERVICE_NAME is beacon, then start beacon with honcho,
# otherwise if RAILWAY_SERVICE_NAME is anchor start anchor
# otherwise raise an error
if [ "$RAILWAY_SERVICE_NAME" = "beacon" ]; then
  honcho start --no-prefix beacon
elif [ "$RAILWAY_SERVICE_NAME" = "anchor" ]; then
    honcho start --no-prefix anchor
  else
    echo "Unknown service name: $RAILWAY_SERVICE_NAME"
    exit 1
fi
