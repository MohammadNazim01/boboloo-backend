#include "ota_validation.h"

#include "esp_log.h"
#include "esp_ota_ops.h"

static const char *TAG = "ota_validation";

/* Cached at boot by ota_boot_check(). */
static bool s_pending_verify = false;

/* ─────────────────────────────────────────────────────────────────────────
 * ota_boot_check
 * Call once at the top of app_main(), before WiFi/MQTT init.
 * ────────────────────────────────────────────────────────────────────────*/
void ota_boot_check(void)
{
    const esp_partition_t *running = esp_ota_get_running_partition();

    esp_ota_img_states_t state;
    esp_err_t err = esp_ota_get_state_partition(running, &state);

    if (err != ESP_OK) {
        /* Not an OTA partition (e.g. factory image) — nothing to verify. */
        s_pending_verify = false;
        ESP_LOGI(TAG, "Running from factory partition — no OTA validation needed");
        return;
    }

    s_pending_verify = (state == ESP_OTA_IMG_PENDING_VERIFY);

    if (s_pending_verify) {
        ESP_LOGW(TAG,
                 "*** OTA PENDING VERIFY: new firmware on partition '%s' ***",
                 running->label);
        ESP_LOGW(TAG,
                 "Will rollback if WiFi + MQTT + heartbeat do not succeed "
                 "before the watchdog fires.");
    } else {
        ESP_LOGI(TAG, "Running firmware is already validated (partition '%s')",
                 running->label);
    }
}

/* ─────────────────────────────────────────────────────────────────────────
 * ota_is_pending_verify
 * ────────────────────────────────────────────────────────────────────────*/
bool ota_is_pending_verify(void)
{
    return s_pending_verify;
}

/* ─────────────────────────────────────────────────────────────────────────
 * ota_mark_valid
 * Call only after WiFi + MQTT + backend heartbeat are all confirmed good.
 * ────────────────────────────────────────────────────────────────────────*/
void ota_mark_valid(void)
{
    if (!s_pending_verify) {
        return;   /* Nothing to do — firmware was already valid at boot. */
    }

    esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    if (err == ESP_OK) {
        s_pending_verify = false;
        ESP_LOGI(TAG, "Firmware marked VALID — rollback protection cancelled");
    } else {
        ESP_LOGE(TAG, "esp_ota_mark_app_valid_cancel_rollback failed: %s",
                 esp_err_to_name(err));
    }
}

/* ─────────────────────────────────────────────────────────────────────────
 * ota_rollback
 * Marks the running firmware invalid and reboots to the previous partition.
 * This function does not return.
 * ────────────────────────────────────────────────────────────────────────*/
void ota_rollback(const char *reason)
{
    ESP_LOGE(TAG, "OTA ROLLBACK triggered: %s", reason ? reason : "unknown");

    /*
     * esp_ota_mark_app_invalid_rollback_and_reboot() marks the current
     * partition as ESP_OTA_IMG_INVALID, then immediately reboots.
     * The bootloader will select the previous valid OTA partition.
     *
     * If there is no valid previous partition, the bootloader falls back
     * to the factory partition (our recovery image).
     */
    esp_ota_mark_app_invalid_rollback_and_reboot();

    /* unreachable — reboot triggered above */
    for (;;) { }
}
