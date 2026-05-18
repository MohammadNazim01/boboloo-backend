#pragma once

#include "esp_err.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Parameters delivered via MQTT cmd topic.
 * JSON shape: {"type":"ota","version":"1.2.3","url":"https://...","sha256":"abc...","size":1234567}
 */
typedef struct {
    char url[512];      // HTTPS pre-signed S3 URL
    char sha256[65];    // 64 hex chars + null terminator
    char version[32];   // semantic version string
    uint32_t size;      // expected binary size in bytes (0 = unknown)
} ota_params_t;

/**
 * Download firmware from params->url over HTTPS, compute SHA256 on the fly,
 * verify against params->sha256, flash to the inactive OTA partition,
 * then call esp_ota_set_boot_partition() and esp_restart().
 *
 * This function only returns on failure. On success it triggers a reboot.
 *
 * Caller is responsible for suspending normal runtime tasks before calling
 * (audio capture, MQTT QoS queues, etc.) to avoid resource contention.
 *
 * Returns:
 *   ESP_ERR_OTA_VALIDATE_FAILED  — SHA256 mismatch (download corrupt/tampered)
 *   ESP_ERR_NO_MEM               — heap exhausted (increase heap or reduce buf)
 *   ESP_FAIL                     — HTTP error, write error, or partition error
 */
esp_err_t ota_download_and_flash(const ota_params_t *params);

#ifdef __cplusplus
}
#endif
