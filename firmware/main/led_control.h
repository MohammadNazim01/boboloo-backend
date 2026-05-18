#pragma once

#include "provisioning_state.h"

/*
 * LED abstraction — drives a single RGB LED (WS2812 / NeoPixel) or three
 * discrete GPIOs depending on the board variant.  Adapt LED_GPIO_* and
 * led_set_rgb() in led_control.c for the specific hardware.
 *
 * All patterns are non-blocking: a FreeRTOS timer drives the blink/pulse
 * cycle.  Call led_set_pattern() from any task.
 */

void led_control_init(void);
void led_set_pattern(led_pattern_t pattern);

/* Convenience wrappers that map provisioning states to LED patterns */
void led_set_prov_state(prov_state_t state, prov_error_t error);
