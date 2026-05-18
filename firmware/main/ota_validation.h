#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Call at the very top of app_main(), before any other initialization.
 *
 * Checks whether the currently running firmware is in PENDING_VERIFY state
 * (i.e. it was just flashed via OTA and has not yet been marked valid).
 * Stores this state internally so ota_is_pending_verify() can be called
 * cheaply throughout the boot sequence.
 */
void ota_boot_check(void);

/**
 * Returns true if this boot is from a freshly-flashed OTA image that has
 * not yet called ota_mark_valid().
 *
 * Use this to decide whether a subsystem failure should trigger rollback:
 *   if (ota_is_pending_verify()) { ota_rollback("wifi_failed"); }
 */
bool ota_is_pending_verify(void);

/**
 * Mark the running firmware as permanently valid.
 *
 * MUST be called only after ALL of these succeed:
 *   1. WiFi connected
 *   2. MQTT connected
 *   3. Backend heartbeat acknowledged (HTTP 200 or MQTT response received)
 *
 * Calling this too early defeats rollback protection.
 * If never called, the watchdog timer will force a reboot and the bootloader
 * will roll back to the previous OTA partition.
 */
void ota_mark_valid(void);

/**
 * Mark the running firmware as invalid and immediately reboot to the
 * previous valid partition (rollback).
 *
 * Call this when a critical subsystem fails during the validation window:
 *   ota_rollback("mqtt_auth_failed");
 *
 * reason is logged before reboot and should be sent via MQTT status if
 * MQTT is available at the point of failure (usually it won't be).
 */
void ota_rollback(const char *reason);

#ifdef __cplusplus
}
#endif
