#include "nvs_storage.h"
#include <nvs_flash.h>
#include <nvs.h>
#include <string.h>
#include <esp_log.h>

#define TAG        "nvs_storage"
#define NVS_NS     "boboloo"
#define KEY_APIKEY "api_key"
#define KEY_SSID   "wifi_ssid"
#define KEY_PASS   "wifi_pass"
#define KEY_PROV   "provisioned"

esp_err_t nvs_storage_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        /* NVS partition truncated or version mismatch — erase and retry */
        ESP_LOGW(TAG, "NVS corrupt, erasing: %s", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    return err;
}

/* ─── Internal helpers ──────────────────────────────────────────────────────── */

static esp_err_t _open(nvs_handle_t *h, nvs_open_mode_t mode)
{
    return nvs_open(NVS_NS, mode, h);
}

static esp_err_t _write_str(nvs_handle_t h, const char *key, const char *val)
{
    esp_err_t err = nvs_set_str(h, key, val);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_set_str(%s) failed: %s", key, esp_err_to_name(err));
    }
    return err;
}

/* ─── Public API ────────────────────────────────────────────────────────────── */

esp_err_t nvs_write_credentials(const char *api_key,
                                const char *ssid,
                                const char *password)
{
    nvs_handle_t h;
    esp_err_t err = _open(&h, NVS_READWRITE);
    if (err != ESP_OK) return err;

    /*
     * Write order: api_key first so that if power is lost mid-write,
     * the "provisioned" flag is never set without the key. The toy
     * checks only the "provisioned" flag at boot.
     */
    if ((err = _write_str(h, KEY_APIKEY, api_key))  != ESP_OK) goto done;
    if ((err = _write_str(h, KEY_SSID,   ssid))     != ESP_OK) goto done;
    if ((err = _write_str(h, KEY_PASS,   password)) != ESP_OK) goto done;

    err = nvs_set_u8(h, KEY_PROV, 1);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_set_u8(provisioned) failed: %s", esp_err_to_name(err));
        goto done;
    }

    err = nvs_commit(h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_commit failed: %s", esp_err_to_name(err));
    }

done:
    nvs_close(h);
    return err;
}

esp_err_t nvs_read_api_key(char *out, size_t len)
{
    nvs_handle_t h;
    esp_err_t err = _open(&h, NVS_READONLY);
    if (err != ESP_OK) return err;
    err = nvs_get_str(h, KEY_APIKEY, out, &len);
    nvs_close(h);
    return err;
}

esp_err_t nvs_read_wifi_ssid(char *out, size_t len)
{
    nvs_handle_t h;
    esp_err_t err = _open(&h, NVS_READONLY);
    if (err != ESP_OK) return err;
    err = nvs_get_str(h, KEY_SSID, out, &len);
    nvs_close(h);
    return err;
}

esp_err_t nvs_read_wifi_pass(char *out, size_t len)
{
    nvs_handle_t h;
    esp_err_t err = _open(&h, NVS_READONLY);
    if (err != ESP_OK) return err;
    err = nvs_get_str(h, KEY_PASS, out, &len);
    nvs_close(h);
    return err;
}

bool nvs_is_provisioned(void)
{
    nvs_handle_t h;
    if (_open(&h, NVS_READONLY) != ESP_OK) return false;

    uint8_t val = 0;
    size_t  len = sizeof(val);
    nvs_get_u8(h, KEY_PROV, &val);
    nvs_close(h);
    return val == 1;
}

esp_err_t nvs_erase_credentials(void)
{
    nvs_handle_t h;
    esp_err_t err = _open(&h, NVS_READWRITE);
    if (err != ESP_OK) return err;
    err = nvs_erase_all(h);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Credentials erased");
    }
    return err;
}
