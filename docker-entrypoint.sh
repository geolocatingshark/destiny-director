#!/bin/sh
source /venv/bin/activate
# If RAILWAY_SERVICE_NAME is conduction-tines, then start conduction with honcho,
# otherwise if RAILWAY_SERVICE_NAME is mortal-polarity start polarity
# otherwise raise an error
if [ "$RAILWAY_SERVICE_NAME" = "conduction-tines" ]; then
  honcho start --no-prefix conduction
elif [ "$RAILWAY_SERVICE_NAME" = "mortal-polarity" ]; then
    honcho start --no-prefix polarity
  else
    echo "Unknown service name: $RAILWAY_SERVICE_NAME"
    exit 1
fi
