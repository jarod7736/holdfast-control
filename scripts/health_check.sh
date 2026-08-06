#!/usr/bin/env bash
curl -sk -o /dev/null -w "%{http_code}\n" http://192.168.1.181:8200/openapi.json && exit 0