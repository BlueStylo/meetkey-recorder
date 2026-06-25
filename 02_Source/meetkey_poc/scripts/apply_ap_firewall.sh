#!/usr/bin/env bash
set -euo pipefail

nft delete table inet meetkey_ap_guard >/dev/null 2>&1 || true
nft add table inet meetkey_ap_guard
nft 'add chain inet meetkey_ap_guard forward { type filter hook forward priority -100; policy accept; }'
nft add rule inet meetkey_ap_guard forward iifname "wlan0" oifname != "wlan0" reject
nft add rule inet meetkey_ap_guard forward oifname "wlan0" iifname != "wlan0" reject
