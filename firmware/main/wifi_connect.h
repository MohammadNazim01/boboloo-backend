#pragma once

#include <esp_err.h>
#include <stdbool.h>

/*
 * WiFi connection + reconnect management.
 *
 * During provisioning validation: if wifi_connect() fails, call
 * wifi_disconnect() and the provisioning state machine rolls back (erases NVS,
 * restarts BLE). After validation is complete and ota_mark_valid() has been
 * called, the reconnect loop handles transient drops transparently.
 */

/* Must be called once before any other wifi_* function. */
void      wifi_connect_init(void);

/* Synchronous connect — blocks until connected or timeout.
 * Returns ESP_OK on success, ESP_ERR_TIMEOUT or ESP_FAIL on failure. */
esp_err_t wifi_connect(const char *ssid, const char *password);

/* Non-blocking disconnect */
void      wifi_disconnect(void);

bool      wifi_is_connected(void);

/*
 * Start background reconnect loop (call after validation succeeds).
 * Retries indefinitely with exponential backoff capped at 60 s.
 */
void      wifi_reconnect_start(void);
void      wifi_reconnect_stop(void);
