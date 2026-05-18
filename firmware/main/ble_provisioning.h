#pragma once

#include "provisioning_state.h"
#include <stdbool.h>

/*
 * BLE provisioning entry point.
 *
 * Call ble_provisioning_start() once at boot when nvs_is_provisioned() == false.
 * The function initialises NimBLE, starts advertising, and runs the state machine
 * to completion.  It returns only when the toy is fully provisioned and validated
 * (PROV_STATE_READY) or when an unrecoverable error forces a restart.
 *
 * On success: BLE is stopped, WiFi + MQTT remain connected, ota_mark_valid() has
 * been called, and the caller should start the normal runtime loop.
 *
 * On failure: NVS is erased, the function restarts BLE and retries from scratch,
 * or reboots after PROV_BLE_TIMEOUT_MS with no connection.
 */
void ble_provisioning_start(const char *factory_device_id,
                            const char *broker_uri,
                            const char *fw_version);

prov_state_t ble_provisioning_get_state(void);
