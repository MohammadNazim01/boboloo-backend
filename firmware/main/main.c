#include <esp_log.h>
#include <esp_system.h>
#include <nvs_flash.h>

#include "nvs_storage.h"
#include "ota_validation.h"
#include "led_control.h"
#include "reset_button.h"
#include "wifi_connect.h"
#include "mqtt_client.h"
#include "ble_provisioning.h"

#define TAG "main"

/* ── Hardware config ─────────────────────────────────────────────────────────── */
#define RESET_BUTTON_GPIO  0   /* BOOT button on most ESP32 dev boards */

/* ── Runtime identity — set once at factory provisioning ─────────────────────── */
#define FACTORY_DEVICE_ID_KEY "factory_id"   /* stored separately in NVS factory ns */

/* These are compiled-in for the runtime path; the BLE provisioning flow writes
 * wifi_ssid, wifi_pass, api_key into the "boboloo" namespace at runtime. */
#define BROKER_URI    CONFIG_BOBOLOO_BROKER_URI    /* set in sdkconfig / menuconfig */
#define FW_VERSION    CONFIG_BOBOLOO_FW_VERSION

/* ─── Inbound MQTT message dispatch ─────────────────────────────────────────── */

static void on_mqtt_message(const char *topic, const char *payload, int len)
{
    ESP_LOGI(TAG, "MQTT rx [%s]: %.*s", topic, len, payload);

    /* cmd topic: handle OTA command from backend */
    if (strstr(topic, "/cmd")) {
        /* ota_update.c parses the JSON and calls ota_download_and_flash() */
        extern void handle_cmd_message(const char *payload, int len);
        handle_cmd_message(payload, len);
    }
    /* audio/out: play TTS response */
    /* TODO: forward to audio subsystem */
}

/* ─── app_main ───────────────────────────────────────────────────────────────── */

void app_main(void)
{
    /* Init NVS first — needed by every subsystem */
    ESP_ERROR_CHECK(nvs_storage_init());

    /* Boot-time OTA validation: mark valid if pending, or rollback if overdue */
    ota_boot_check();

    /* Peripherals */
    led_control_init();
    reset_button_init(RESET_BUTTON_GPIO);
    wifi_connect_init();
    mqtt_client_init();

    /* Read factory_device_id from a separate NVS namespace written at manufacture */
    char device_id[64] = {0};
    {
        nvs_handle_t h;
        if (nvs_open("factory", NVS_READONLY, &h) == ESP_OK) {
            size_t len = sizeof(device_id);
            nvs_get_str(h, FACTORY_DEVICE_ID_KEY, device_id, &len);
            nvs_close(h);
        }
    }

    if (device_id[0] == '\0') {
        ESP_LOGE(TAG, "factory_device_id not set — cannot boot");
        led_set_pattern(LED_FAST_RED_FLASH);
        /* Halt; manufacturing step was incomplete */
        for (;;) vTaskDelay(pdMS_TO_TICKS(1000));
    }

    if (!nvs_is_provisioned()) {
        /*
         * First boot (or after factory reset): run BLE provisioning.
         * This call blocks until the toy is fully provisioned and validated,
         * or restarts the device on timeout.
         */
        ESP_LOGI(TAG, "Starting BLE provisioning for device %s", device_id);
        ble_provisioning_start(device_id, BROKER_URI, FW_VERSION);
        /* Falls through only on success (PROV_STATE_READY) */
    } else {
        /* Already provisioned — connect directly */
        char ssid[WIFI_SSID_MAX + 1] = {0};
        char pass[WIFI_PASS_MAX + 1] = {0};
        char api_key[TOY_API_KEY_SZ] = {0};

        ESP_ERROR_CHECK(nvs_read_wifi_ssid(ssid, sizeof(ssid)));
        ESP_ERROR_CHECK(nvs_read_wifi_pass(pass, sizeof(pass)));
        ESP_ERROR_CHECK(nvs_read_api_key(api_key, sizeof(api_key)));

        ESP_LOGI(TAG, "Connecting to WiFi: %s", ssid);
        esp_err_t err = wifi_connect(ssid, pass);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "WiFi failed (%s) — restarting", esp_err_to_name(err));
            esp_restart();
        }

        err = mqtt_connect(BROKER_URI, device_id, api_key);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "MQTT failed (%s) — restarting", esp_err_to_name(err));
            esp_restart();
        }

        mqtt_subscribe_toy_topics(device_id);
        mqtt_set_message_callback(on_mqtt_message);
        wifi_reconnect_start();

        ota_mark_valid();
        led_set_prov_state(PROV_STATE_READY, PROV_ERR_NONE);
        mqtt_send_heartbeat(device_id, FW_VERSION);
    }

    /* Runtime loop — BLE is stopped, WiFi + MQTT are up */
    ESP_LOGI(TAG, "Runtime loop started");
    mqtt_set_message_callback(on_mqtt_message);

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(30000));
        /* Periodic heartbeat */
        mqtt_send_heartbeat(device_id, FW_VERSION);
    }
}
