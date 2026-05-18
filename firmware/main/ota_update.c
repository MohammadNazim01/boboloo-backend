#include "ota_update.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "mbedtls/sha256.h"

static const char *TAG = "ota_update";

#define OTA_BUF_SIZE    (4096)
#define HTTP_TIMEOUT_MS (30000)

/* ── Forward declarations ───────────────────────────────────────────────── */
static bool _hex_sha256_match(const uint8_t digest[32], const char *expected_hex);

/* ─────────────────────────────────────────────────────────────────────────
 * ota_download_and_flash
 * ────────────────────────────────────────────────────────────────────────*/
esp_err_t ota_download_and_flash(const ota_params_t *params)
{
    esp_err_t           err;
    esp_ota_handle_t    ota_handle  = 0;
    esp_http_client_handle_t client = NULL;
    uint8_t            *buf         = NULL;

    /* ── 1. Select target partition ─────────────────────────────────────── */
    const esp_partition_t *update_partition =
        esp_ota_get_next_update_partition(NULL);

    if (!update_partition) {
        ESP_LOGE(TAG, "No OTA partition available");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Flashing to partition: %s (offset=0x%08lx size=0x%08lx)",
             update_partition->label,
             update_partition->address,
             update_partition->size);

    /* ── 2. Begin OTA write ─────────────────────────────────────────────── */
    err = esp_ota_begin(update_partition, OTA_WITH_SEQUENTIAL_WRITES, &ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_begin failed: %s", esp_err_to_name(err));
        return err;
    }

    /* ── 3. Open HTTPS connection ───────────────────────────────────────── */
    esp_http_client_config_t http_cfg = {
        .url               = params->url,
        .crt_bundle_attach = esp_crt_bundle_attach,   // uses bundled Mozilla root CAs
        .timeout_ms        = HTTP_TIMEOUT_MS,
        .buffer_size       = OTA_BUF_SIZE,
        .keep_alive_enable = false,
    };

    client = esp_http_client_init(&http_cfg);
    if (!client) {
        ESP_LOGE(TAG, "HTTP client init failed");
        err = ESP_FAIL;
        goto abort_ota;
    }

    err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP open failed: %s", esp_err_to_name(err));
        goto cleanup;
    }

    esp_http_client_fetch_headers(client);

    int http_status = esp_http_client_get_status_code(client);
    if (http_status != 200) {
        ESP_LOGE(TAG, "HTTP status %d — aborting OTA", http_status);
        err = ESP_FAIL;
        goto cleanup;
    }

    /* ── 4. Stream download: write to flash + accumulate SHA256 ─────────── */
    buf = (uint8_t *)malloc(OTA_BUF_SIZE);
    if (!buf) {
        ESP_LOGE(TAG, "OTA buffer malloc failed (%d bytes)", OTA_BUF_SIZE);
        err = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    mbedtls_sha256_context sha_ctx;
    mbedtls_sha256_init(&sha_ctx);
    mbedtls_sha256_starts(&sha_ctx, 0);   /* 0 = SHA-256 (not SHA-224) */

    uint32_t total_written = 0;
    int      read_len;

    while ((read_len = esp_http_client_read(client, (char *)buf, OTA_BUF_SIZE)) > 0) {
        mbedtls_sha256_update(&sha_ctx, buf, (size_t)read_len);

        err = esp_ota_write(ota_handle, buf, (size_t)read_len);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_ota_write failed at %lu bytes: %s",
                     total_written, esp_err_to_name(err));
            mbedtls_sha256_free(&sha_ctx);
            goto cleanup;
        }

        total_written += (uint32_t)read_len;
    }

    if (read_len < 0) {
        ESP_LOGE(TAG, "HTTP read error: %d", read_len);
        mbedtls_sha256_free(&sha_ctx);
        err = ESP_FAIL;
        goto cleanup;
    }

    ESP_LOGI(TAG, "Downloaded %lu bytes", total_written);

    /* ── 5. Verify SHA256 ───────────────────────────────────────────────── */
    uint8_t digest[32];
    mbedtls_sha256_finish(&sha_ctx, digest);
    mbedtls_sha256_free(&sha_ctx);

    if (!_hex_sha256_match(digest, params->sha256)) {
        ESP_LOGE(TAG, "SHA256 mismatch — firmware rejected");
        err = ESP_ERR_OTA_VALIDATE_FAILED;
        goto cleanup;
    }

    ESP_LOGI(TAG, "SHA256 verified OK");

    /* ── 6. Finalise OTA ────────────────────────────────────────────────── */
    free(buf);
    buf = NULL;
    esp_http_client_cleanup(client);
    client = NULL;

    err = esp_ota_end(ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_end failed: %s", esp_err_to_name(err));
        return err;
    }

    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_set_boot_partition failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "OTA complete — rebooting to %s (version %s)",
             update_partition->label, params->version);

    esp_restart();
    /* unreachable */
    return ESP_OK;

cleanup:
    if (buf)    free(buf);
    if (client) esp_http_client_cleanup(client);
abort_ota:
    esp_ota_abort(ota_handle);
    return err;
}

/* ── Helpers ────────────────────────────────────────────────────────────── */

static bool _hex_sha256_match(const uint8_t digest[32], const char *expected_hex)
{
    char computed[65];
    for (int i = 0; i < 32; i++) {
        snprintf(&computed[i * 2], 3, "%02x", digest[i]);
    }
    computed[64] = '\0';

    bool match = (strcasecmp(computed, expected_hex) == 0);
    if (!match) {
        ESP_LOGE("ota_update", "SHA256 expected: %s", expected_hex);
        ESP_LOGE("ota_update", "SHA256 computed: %s", computed);
    }
    return match;
}
