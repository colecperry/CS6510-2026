#!/usr/bin/env bash
# Walks the whole public contract through the edge: all seven endpoints and all six error codes.
#
# Exists because the load client only exercises the happy path. Every refusal the contract defines has to be checked by hand, and this week is the first where a payment returns before its effect has happened, so the shape of a receipt is worth re-checking rather than assuming.
# Prints one line per case and exits non-zero if any of them is wrong, so it can be used as a gate as well as read.
set -u
BASE=${BASE:-http://localhost:8080}
failures=0

# Runs one case: a label, the status code expected, the error code expected (or "-" for a success), then the curl arguments.
check () {
    local label=$1 want_status=$2 want_error=$3; shift 3
    local body status error
    body=$(curl -s -o /tmp/smoke_body -w '%{http_code}' "$@")
    status=$body
    body=$(cat /tmp/smoke_body)
    error=$(printf '%s' "$body" | python3 -c 'import sys,json
try:
    print(json.load(sys.stdin).get("error","-"))
except Exception:
    print("-")')

    if [ "$status" = "$want_status" ] && { [ "$want_error" = "-" ] || [ "$error" = "$want_error" ]; }; then
        printf '  ok    %-42s %s %s\n' "$label" "$status" "$error"
    else
        printf '  FAIL  %-42s got %s %s, wanted %s %s\n' "$label" "$status" "$error" "$want_status" "$want_error"
        failures=$((failures + 1))
    fi
}

# Pulls one field out of the last response body, so a transaction id can be carried into the next case.
field () { python3 -c "import json;print(json.load(open('/tmp/smoke_body'))$1)"; }

echo "the seven endpoints"
check "POST /transactions"                      201 - -X POST "$BASE/transactions" -H 'content-type: application/json' -d '{"stationId":"smoke-1"}'
TX=$(field "['transactionId']")

check "POST /transactions/{id}/items"           200 - -X POST "$BASE/transactions/$TX/items" -H 'content-type: application/json' -d '{"sku":"SKU-000001","quantity":1}'
check "GET  /transactions/{id}"                 200 - "$BASE/transactions/$TX"
check "POST /transactions/{id}/complete"        200 - -X POST "$BASE/transactions/$TX/complete"
LINES=$(field "['lines'][0]['quantity']")
check "GET  /items"                             200 - "$BASE/items"
check "GET  /inventory/low-stock"               200 - "$BASE/inventory/low-stock?threshold=50"
check "GET  /analytics/popular-items"           200 - "$BASE/analytics/popular-items?limit=5"

echo
echo "the six refusals"
check "unknown SKU"                             404 UNKNOWN_SKU           -X POST "$BASE/transactions/$TX/items" -H 'content-type: application/json' -d '{"sku":"SKU-nope","quantity":1}'
check "scanning a closed basket"                409 TRANSACTION_NOT_OPEN   -X POST "$BASE/transactions/$TX/items" -H 'content-type: application/json' -d '{"sku":"SKU-000001","quantity":1}'
check "paying twice"                            409 TRANSACTION_NOT_OPEN   -X POST "$BASE/transactions/$TX/complete"
check "unknown transaction"                     404 NOT_FOUND              "$BASE/transactions/tx-does-not-exist"
check "missing stationId"                       400 INVALID_REQUEST        -X POST "$BASE/transactions" -H 'content-type: application/json' -d '{"stationId":"  "}'

# Needs its own basket, because the refusal only happens while one is still empty.
curl -s -o /tmp/smoke_body -X POST "$BASE/transactions" -H 'content-type: application/json' -d '{"stationId":"smoke-2"}' >/dev/null
EMPTY=$(field "['transactionId']")
check "paying for an empty basket"              409 EMPTY_BASKET           -X POST "$BASE/transactions/$EMPTY/complete"

echo
echo "nothing private is reachable from outside"
check "internal paths are not routed"           404 NOT_FOUND              "$BASE/internal/scans"

echo
echo "the receipt collapsed its lines: quantity $LINES"
if [ "$failures" -gt 0 ]; then
    echo "$failures case(s) wrong"
    exit 1
fi
echo "all cases correct"
