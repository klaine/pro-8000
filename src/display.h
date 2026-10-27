// The 4-digit 7-segment display and the button LEDs, all on the HT16K33 (mapped with tools/web, 2026-10-27).
#ifndef DISPLAY_H
#define DISPLAY_H

typedef enum {
    LED_PRESET, LED_PERF, LED_SETTINGS, LED_TUNE, LED_SYNC_SOURCE, LED_SYNC_CLOCK, LED_REC,
    LED_SEQ1, LED_SEQ2, LED_ARP_UP, LED_ARP_ASSIGN, LED_POWER, LED_COUNT
} led_t;

void display_clear(void);
// Up to 4 characters; a '.' lights the dot after the character before it. Unknown characters show blank.
void display_text(const char *text);
void display_number(int value); // right-aligned, -999..9999
void display_led(led_t led, int on);
// Writes the display RAM if anything changed (an I2C transfer of about 3 ms).
void display_update(void);
// Writes it regardless, after something else (raw RAM writes) changed the display.
void display_redraw(void);

#endif
