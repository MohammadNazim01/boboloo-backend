#pragma once

#include <esp_err.h>
#include <stdbool.h>

/*
 * NVS namespace: "boboloo"
 * Keys and write order (MUST be preserved — api_key first, then ssid, password, provisioned):
 *   "api_key"     — toy API key (64-char hex string)
 *   "wifi_ssid"   — WiFi SSID (max 32 chars)
 *   "wifi_pass"   — WiFi password (max 64 chars)
 *   "provisioned" — uint8, written last; 1 = provisioning complete
 *
 * NVS partition is encrypted via nvs_keys partition (AES-256 XTS).
 * The encrypted partition handle is opened once and reused.
 */

esp_err_t nvs_storage_init(void);

/* Write order: api_key → ssid → password → set provisioned flag */
esp_err_t nvs_write_credentials(const char *api_key,
                                const char *ssid,
                                const char *password);

esp_err_t nvs_read_api_key(char *out, size_t len);
esp_err_t nvs_read_wifi_ssid(char *out, size_t len);
esp_err_t nvs_read_wifi_pass(char *out, size_t len);

/* Returns true if "provisioned" key == 1 */
bool      nvs_is_provisioned(void);

/* Erases the entire "boboloo" namespace — used for factory reset */
esp_err_t nvs_erase_credentials(void);
