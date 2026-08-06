#!/usr/bin/env bash
curl -s --max-time 10 -o /dev/null -w "%{http_code}\n" https://holdfast.tail1c66ec.ts.net/openapi.json && exit 0