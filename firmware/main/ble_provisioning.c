#include "ble_provisioning.h"
#include "provisioning_state.h"
#include "nvs_storage.h"
#include "led_control.h"
#include "wifi_connect.h"
#include "mqtt_client.h"
#include "ota_validation.h"

#include <nimble/nimble_port.h>
#include <nimble/nimble_port_freertos.h>
#include <host/ble_hs.h>
#include <host/ble_gap.h>
#include <host/ble_gatt.h>
#include <host/util/util.h>
#include <services/gap/ble_svc_gap.h>
#include <services/gatt/ble_svc_gatt.h>

#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <freertos/timers.h>
#include <esp_log.h>
#include <esp_system.h>
#include <string.h>
#include <stdio.h>

#define TAG "ble_prov"

/* ─── State ─────────────────────────────────────────────────────────────────── */

static prov_state_t      s_state          = PROV_STATE_UNPROVISIONED;
static prov_error_t      s_error          = PROV_ERR_NONE;
static prov_credentials_t s_creds         = {0};
static uint16_t          s_conn_handle    = BLE_HS_CONN_HANDLE_NONE;
static uint16_t          s_status_val_hdl = 0;

/* Context strings set at start */
static char s_device_id[64]   = {0};
static char s_broker_uri[128] = {0};
static char s_fw_version[32]  = {0};

/* BLE timeout timer */
static TimerHandle_t s_ble_timeout_timer = NULL;

/* Event group for state machine synchronisation */
#define EV_COMMIT_CMD  BIT0
#define EV_CANCEL_CMD  BIT1
#define EV_BLE_TIMEOUT BIT2
#define EV_BLE_DISC    BIT3
static EventGroupHandle_t s_ev = NULL;

/* ─── GATT characteristic value handles (filled by NimBLE after registration) ── */
static uint16_t s_hdl_device_info = 0;
static uint16_t s_hdl_wifi_ssid   = 0;
static uint16_t s_hdl_wifi_pass   = 0;
static uint16_t s_hdl_api_key     = 0;
static uint16_t s_hdl_prov_cmd    = 0;

/* ─── Helpers ───────────────────────────────────────────────────────────────── */

static void set_state(prov_state_t state, prov_error_t error)
{
    s_state = state;
    s_error = error;
    led_set_prov_state(state, error);
    ESP_LOGI(TAG, "State → %d (err=%d)", state, error);

    /* Notify the BLE central if it is subscribed to PROV_STATUS */
    if (s_conn_handle != BLE_HS_CONN_HANDLE_NONE && s_status_val_hdl) {
        prov_ble_status_t notif = {
            .state = (uint8_t)state,
            .error = (uint8_t)error,
        };
        struct os_mbuf *om = ble_hs_mbuf_from_flat(&notif, sizeof(notif));
        if (om) {
            ble_gattc_notify_custom(s_conn_handle, s_status_val_hdl, om);
        }
    }
}

static bool all_creds_loaded(void)
{
    return s_creds.ssid_set && s_creds.pass_set && s_creds.api_key_set;
}

/* ─── BLE timeout ───────────────────────────────────────────────────────────── */

static void ble_timeout_cb(TimerHandle_t t)
{
    ESP_LOGW(TAG, "BLE advertising timeout — rebooting");
    xEventGroupSetBits(s_ev, EV_BLE_TIMEOUT);
}

/* ─── GATT characteristic access callbacks ──────────────────────────────────── */

static int char_device_info_access(uint16_t conn, uint16_t attr,
                                   struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op != BLE_GATT_ACCESS_OP_READ_CHR) return BLE_ATT_ERR_WRITE_NOT_PERMITTED;

    char buf[128];
    snprintf(buf, sizeof(buf),
             "{\"factory_device_id\":\"%s\",\"fw_version\":\"%s\"}",
             s_device_id, s_fw_version);
    return os_mbuf_append(ctxt->om, buf, strlen(buf)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static int char_wifi_ssid_access(uint16_t conn, uint16_t attr,
                                 struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_READ_NOT_PERMITTED;

    uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
    if (len == 0 || len > WIFI_SSID_MAX) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;

    memset(s_creds.ssid, 0, sizeof(s_creds.ssid));
    os_mbuf_copydata(ctxt->om, 0, len, s_creds.ssid);
    s_creds.ssid_set = 1;
    ESP_LOGI(TAG, "SSID received (%d bytes)", len);

    if (all_creds_loaded()) set_state(PROV_STATE_CREDS_LOADED, PROV_ERR_NONE);
    return 0;
}

static int char_wifi_pass_access(uint16_t conn, uint16_t attr,
                                 struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_READ_NOT_PERMITTED;

    uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
    if (len == 0 || len > WIFI_PASS_MAX) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;

    memset(s_creds.password, 0, sizeof(s_creds.password));
    os_mbuf_copydata(ctxt->om, 0, len, s_creds.password);
    s_creds.pass_set = 1;
    ESP_LOGI(TAG, "Password received (%d bytes)", len);

    if (all_creds_loaded()) set_state(PROV_STATE_CREDS_LOADED, PROV_ERR_NONE);
    return 0;
}

static int char_api_key_access(uint16_t conn, uint16_t attr,
                               struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_READ_NOT_PERMITTED;

    uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
    if (len != TOY_API_KEY_LEN) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;

    memset(s_creds.api_key, 0, sizeof(s_creds.api_key));
    os_mbuf_copydata(ctxt->om, 0, len, s_creds.api_key);
    s_creds.api_key_set = 1;
    ESP_LOGI(TAG, "API key received");

    if (all_creds_loaded()) set_state(PROV_STATE_CREDS_LOADED, PROV_ERR_NONE);
    return 0;
}

static int char_prov_cmd_access(uint16_t conn, uint16_t attr,
                                struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) return BLE_ATT_ERR_READ_NOT_PERMITTED;

    uint8_t cmd = 0;
    os_mbuf_copydata(ctxt->om, 0, 1, &cmd);

    if (cmd == PROV_CMD_COMMIT) {
        if (!all_creds_loaded()) {
            ESP_LOGW(TAG, "COMMIT received before all creds loaded");
            return BLE_ATT_ERR_UNLIKELY;
        }
        xEventGroupSetBits(s_ev, EV_COMMIT_CMD);
    } else if (cmd == PROV_CMD_CANCEL) {
        xEventGroupSetBits(s_ev, EV_CANCEL_CMD);
    }
    return 0;
}

static int char_status_access(uint16_t conn, uint16_t attr,
                              struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    /* Central reads initial value; notifications carry updates */
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        prov_ble_status_t st = {.state = s_state, .error = s_error};
        return os_mbuf_append(ctxt->om, &st, sizeof(st)) == 0
               ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return 0;
}

/* ─── GATT service definition ───────────────────────────────────────────────── */

static const ble_uuid128_t svc_uuid = {
    .u   = {.type = BLE_UUID_TYPE_128},
    .value = PROV_SVC_UUID128,
};

#define BLE_UUID16_DECLARE_VAL(v) \
    ((ble_uuid_t *)(&(ble_uuid16_t){.u={.type=BLE_UUID_TYPE_16},.value=(v)}))

static const struct ble_gatt_svc_def s_gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid       = BLE_UUID16_DECLARE_VAL(CHAR_UUID_DEVICE_INFO),
                .access_cb  = char_device_info_access,
                .val_handle = &s_hdl_device_info,
                .flags      = BLE_GATT_CHR_F_READ,
            },
            {
                .uuid       = BLE_UUID16_DECLARE_VAL(CHAR_UUID_WIFI_SSID),
                .access_cb  = char_wifi_ssid_access,
                .val_handle = &s_hdl_wifi_ssid,
                .flags      = BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid       = BLE_UUID16_DECLARE_VAL(CHAR_UUID_WIFI_PASS),
                .access_cb  = char_wifi_pass_access,
                .val_handle = &s_hdl_wifi_pass,
                .flags      = BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid       = BLE_UUID16_DECLARE_VAL(CHAR_UUID_TOY_API_KEY),
                .access_cb  = char_api_key_access,
                .val_handle = &s_hdl_api_key,
                .flags      = BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid       = BLE_UUID16_DECLARE_VAL(CHAR_UUID_PROV_CMD),
                .access_cb  = char_prov_cmd_access,
                .val_handle = &s_hdl_prov_cmd,
                .flags      = BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid       = BLE_UUID16_DECLARE_VAL(CHAR_UUID_PROV_STATUS),
                .access_cb  = char_status_access,
                .val_handle = &s_status_val_hdl,
                .flags      = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
            },
            { 0 }, /* terminator */
        },
    },
    { 0 }, /* terminator */
};

/* ─── GAP callbacks ─────────────────────────────────────────────────────────── */

static int gap_event_handler(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            s_conn_handle = event->connect.conn_handle;
            set_state(PROV_STATE_BLE_CONNECTED, PROV_ERR_NONE);
            ESP_LOGI(TAG, "Central connected, handle=%d", s_conn_handle);
        }
        break;

    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGW(TAG, "Central disconnected, reason=%d",
                 event->disconnect.reason);
        s_conn_handle = BLE_HS_CONN_HANDLE_NONE;

        /* Only signal disconnect if we haven't committed yet */
        if (s_state < PROV_STATE_COMMITTING) {
            xEventGroupSetBits(s_ev, EV_BLE_DISC);
        }
        break;

    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "MTU updated: %d", event->mtu.value);
        break;

    default:
        break;
    }
    return 0;
}

/* ─── Advertising ───────────────────────────────────────────────────────────── */

static void start_advertising(void)
{
    struct ble_gap_adv_params adv_params = {
        .conn_mode  = BLE_GAP_CONN_MODE_UND,
        .disc_mode  = BLE_GAP_DISC_MODE_GEN,
        .itvl_min   = BLE_GAP_ADV_ITVL_MS(100),
        .itvl_max   = BLE_GAP_ADV_ITVL_MS(200),
    };

    /* Advertise name + service UUID */
    struct ble_hs_adv_fields adv_fields = {0};
    uint8_t name_len = (uint8_t)strlen(s_device_id);
    adv_fields.name            = (const uint8_t *)s_device_id;
    adv_fields.name_len        = name_len > 8 ? 8 : name_len; /* limit to 8 chars */
    adv_fields.name_is_complete = 1;
    adv_fields.flags           = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;

    ble_gap_adv_set_fields(&adv_fields);

    int rc = ble_gap_adv_start(BLE_OWN_ADDR_PUBLIC, NULL, BLE_HS_FOREVER,
                               &adv_params, gap_event_handler, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_gap_adv_start failed: %d", rc);
    } else {
        ESP_LOGI(TAG, "BLE advertising as %s", s_device_id);
    }
}

/* ─── NimBLE host task ──────────────────────────────────────────────────────── */

static void nimble_host_task(void *param)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static void on_ble_stack_reset(int reason)
{
    ESP_LOGW(TAG, "BLE host reset (reason=%d)", reason);
}

static void on_ble_stack_sync(void)
{
    ble_hs_util_ensure_addr(0);
    start_advertising();
}

/* ─── Rollback helper ───────────────────────────────────────────────────────── */

static void rollback_and_restart_ble(prov_error_t error)
{
    set_state(PROV_STATE_ERROR, error);

    ESP_LOGW(TAG, "Rolling back: erasing NVS, restarting BLE");
    nvs_erase_credentials();
    memset(&s_creds, 0, sizeof(s_creds));

    /* Clear error and return to advertising */
    s_error = PROV_ERR_NONE;
    set_state(PROV_STATE_UNPROVISIONED, PROV_ERR_NONE);
    start_advertising();
}

/* ─── Main provisioning state machine ──────────────────────────────────────── */

static void run_provisioning_loop(void)
{
    for (;;) {
        EventBits_t ev = xEventGroupWaitBits(
            s_ev,
            EV_COMMIT_CMD | EV_CANCEL_CMD | EV_BLE_TIMEOUT | EV_BLE_DISC,
            pdTRUE, pdFALSE,
            portMAX_DELAY);

        if (ev & EV_BLE_TIMEOUT) {
            ESP_LOGW(TAG, "BLE timeout — no provisioning completed");
            set_state(PROV_STATE_ERROR, PROV_ERR_BLE_TIMEOUT);
            vTaskDelay(pdMS_TO_TICKS(2000));
            esp_restart();
        }

        if (ev & EV_CANCEL_CMD) {
            ESP_LOGI(TAG, "Provisioning cancelled by app");
            rollback_and_restart_ble(PROV_ERR_NONE);
            continue;
        }

        if (ev & EV_BLE_DISC) {
            /* Central disconnected before commit; reset RAM creds, re-advertise */
            ESP_LOGW(TAG, "BLE disconnected mid-provisioning, resetting creds");
            memset(&s_creds, 0, sizeof(s_creds));
            set_state(PROV_STATE_UNPROVISIONED, PROV_ERR_NONE);
            start_advertising();
            continue;
        }

        if (!(ev & EV_COMMIT_CMD)) continue;

        /* ── COMMITTING ─────────────────────────────────────────────────────── */
        set_state(PROV_STATE_COMMITTING, PROV_ERR_NONE);

        esp_err_t err = nvs_write_credentials(s_creds.api_key,
                                              s_creds.ssid,
                                              s_creds.password);
        if (err != ESP_OK) {
            rollback_and_restart_ble(PROV_ERR_NVS_WRITE);
            continue;
        }

        /* ── WIFI_CONNECTING ────────────────────────────────────────────────── */
        set_state(PROV_STATE_WIFI_CONNECTING, PROV_ERR_NONE);
        err = wifi_connect(s_creds.ssid, s_creds.password);

        if (err == ESP_FAIL) {
            rollback_and_restart_ble(PROV_ERR_WIFI_AUTH);
            continue;
        }
        if (err == ESP_ERR_TIMEOUT) {
            rollback_and_restart_ble(PROV_ERR_WIFI_TIMEOUT);
            continue;
        }

        /* ── MQTT_CONNECTING ─────────────────────────────────────────────────── */
        set_state(PROV_STATE_MQTT_CONNECTING, PROV_ERR_NONE);
        err = mqtt_connect(s_broker_uri, s_device_id, s_creds.api_key);

        if (err != ESP_OK) {
            wifi_disconnect();
            rollback_and_restart_ble(
                err == ESP_FAIL ? PROV_ERR_MQTT_AUTH : PROV_ERR_MQTT_TIMEOUT);
            continue;
        }

        mqtt_subscribe_toy_topics(s_device_id);

        /* ── VALIDATING ──────────────────────────────────────────────────────── */
        set_state(PROV_STATE_VALIDATING, PROV_ERR_NONE);
        err = mqtt_send_heartbeat(s_device_id, s_fw_version);

        if (err != ESP_OK) {
            mqtt_disconnect();
            wifi_disconnect();
            rollback_and_restart_ble(PROV_ERR_HEARTBEAT_TIMEOUT);
            continue;
        }

        /*
         * Give the broker 10 s to acknowledge the heartbeat (no ack expected
         * at QoS 0 — we simply trust the publish succeeded via the connected
         * state; for production add a backend ACK characteristic if needed).
         */
        vTaskDelay(pdMS_TO_TICKS(PROV_HEARTBEAT_TIMEOUT_MS));

        if (!mqtt_is_connected()) {
            mqtt_disconnect();
            wifi_disconnect();
            rollback_and_restart_ble(PROV_ERR_MQTT_TIMEOUT);
            continue;
        }

        /* ── READY ───────────────────────────────────────────────────────────── */
        ota_mark_valid();
        wifi_reconnect_start();
        set_state(PROV_STATE_READY, PROV_ERR_NONE);

        ESP_LOGI(TAG, "Provisioning complete — stopping BLE");
        xTimerStop(s_ble_timeout_timer, 0);
        ble_gap_adv_stop();

        /* Disconnect the provisioning central gracefully */
        if (s_conn_handle != BLE_HS_CONN_HANDLE_NONE) {
            ble_gap_terminate(s_conn_handle, BLE_ERR_REM_USER_CONN_TERM);
        }

        nimble_port_stop();
        return;
    }
}

/* ─── Public entry point ────────────────────────────────────────────────────── */

void ble_provisioning_start(const char *factory_device_id,
                            const char *broker_uri,
                            const char *fw_version)
{
    strncpy(s_device_id,   factory_device_id, sizeof(s_device_id) - 1);
    strncpy(s_broker_uri,  broker_uri,        sizeof(s_broker_uri) - 1);
    strncpy(s_fw_version,  fw_version,        sizeof(s_fw_version) - 1);

    s_ev = xEventGroupCreate();

    /* NimBLE init */
    esp_nimble_hci_and_controller_init();
    nimble_port_init();

    ble_hs_cfg.reset_cb  = on_ble_stack_reset;
    ble_hs_cfg.sync_cb   = on_ble_stack_sync;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;

    ble_svc_gap_init();
    ble_svc_gatt_init();

    ble_svc_gap_device_name_set(factory_device_id);

    int rc = ble_gatts_count_cfg(s_gatt_svcs);
    assert(rc == 0);
    rc = ble_gatts_add_svcs(s_gatt_svcs);
    assert(rc == 0);

    nimble_port_freertos_init(nimble_host_task);

    /* 10-min advertising timeout */
    s_ble_timeout_timer = xTimerCreate("ble_timeout",
                                       pdMS_TO_TICKS(PROV_BLE_TIMEOUT_MS),
                                       pdFALSE, NULL, ble_timeout_cb);
    xTimerStart(s_ble_timeout_timer, 0);

    set_state(PROV_STATE_UNPROVISIONED, PROV_ERR_NONE);

    /* Blocks until provisioning is complete or device restarts */
    run_provisioning_loop();
}

prov_state_t ble_provisioning_get_state(void)
{
    return s_state;
}
