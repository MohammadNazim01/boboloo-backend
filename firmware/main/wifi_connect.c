#include "wifi_connect.h"
#include "provisioning_state.h"
#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <freertos/task.h>
#include <esp_wifi.h>
#include <esp_event.h>
#include <esp_log.h>
#include <string.h>

#define TAG "wifi_connect"

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

static EventGroupHandle_t  s_wifi_events   = NULL;
static bool                s_reconnect     = false;
static char                s_ssid[WIFI_SSID_MAX + 1];
static char                s_pass[WIFI_PASS_MAX + 1];
static int                 s_retry_count   = 0;
static TaskHandle_t        s_reconnect_task = NULL;

/* ─── Event handler ─────────────────────────────────────────────────────────── */

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_reconnect) {
            /* Reconnect loop task handles retries */
            xEventGroupSetBits(s_wifi_events, WIFI_FAIL_BIT);
        } else {
            s_retry_count++;
            if (s_retry_count < PROV_WIFI_MAX_RETRIES) {
                esp_wifi_connect();
                ESP_LOGI(TAG, "Retry %d/%d", s_retry_count, PROV_WIFI_MAX_RETRIES);
            } else {
                xEventGroupSetBits(s_wifi_events, WIFI_FAIL_BIT);
            }
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_retry_count = 0;
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
    }
}

/* ─── Reconnect background task ─────────────────────────────────────────────── */

static void reconnect_task(void *arg)
{
    uint32_t backoff_ms = 2000;
    for (;;) {
        EventBits_t bits = xEventGroupWaitBits(
            s_wifi_events, WIFI_FAIL_BIT, pdTRUE, pdFALSE, portMAX_DELAY);

        if (!(bits & WIFI_FAIL_BIT)) continue;

        ESP_LOGI(TAG, "WiFi lost, reconnecting in %lu ms", (unsigned long)backoff_ms);
        vTaskDelay(pdMS_TO_TICKS(backoff_ms));
        esp_wifi_connect();

        /* Exponential backoff: 2 s → 4 → 8 → … → 60 s */
        backoff_ms = backoff_ms < 60000 ? backoff_ms * 2 : 60000;
    }
}

/* ─── Public API ────────────────────────────────────────────────────────────── */

void wifi_connect_init(void)
{
    s_wifi_events = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                        wifi_event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                        wifi_event_handler, NULL, NULL);

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
}

esp_err_t wifi_connect(const char *ssid, const char *password)
{
    strncpy(s_ssid, ssid,     sizeof(s_ssid) - 1);
    strncpy(s_pass, password, sizeof(s_pass) - 1);
    s_retry_count = 0;
    s_reconnect   = false;
    xEventGroupClearBits(s_wifi_events, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT);

    wifi_config_t wcfg = {0};
    strncpy((char *)wcfg.sta.ssid,     ssid,     sizeof(wcfg.sta.ssid) - 1);
    strncpy((char *)wcfg.sta.password, password, sizeof(wcfg.sta.password) - 1);
    wcfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wcfg.sta.pmf_cfg.capable    = true;
    wcfg.sta.pmf_cfg.required   = false;

    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wcfg));
    esp_wifi_connect();

    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_events,
        WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
        pdFALSE, pdFALSE,
        pdMS_TO_TICKS(PROV_WIFI_CONNECT_TIMEOUT_MS));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "Connected to %s", ssid);
        return ESP_OK;
    }
    if (bits & WIFI_FAIL_BIT) {
        ESP_LOGW(TAG, "Failed to connect to %s", ssid);
        return ESP_FAIL;
    }
    ESP_LOGW(TAG, "WiFi connect timeout");
    return ESP_ERR_TIMEOUT;
}

void wifi_disconnect(void)
{
    s_reconnect = false;
    esp_wifi_disconnect();
}

bool wifi_is_connected(void)
{
    return (xEventGroupGetBits(s_wifi_events) & WIFI_CONNECTED_BIT) != 0;
}

void wifi_reconnect_start(void)
{
    s_reconnect = true;
    if (!s_reconnect_task) {
        xTaskCreate(reconnect_task, "wifi_reconnect", 2048, NULL, 5, &s_reconnect_task);
    }
}

void wifi_reconnect_stop(void)
{
    s_reconnect = false;
    if (s_reconnect_task) {
        vTaskDelete(s_reconnect_task);
        s_reconnect_task = NULL;
    }
}
