// HT16K33 RAM layout, mapped by lighting one bit at a time:
//   digits left to right at RAM 2, 0, 6, 4 (COM1, COM0, COM3, COM2); each digit uses its address for segments
//   a, b, g (bits 0-2) and the next address for f, e, d, c and the dot after it (bits 3-7)
//   LEDs at RAM 8-15 (COM4-COM7), see `leds`
#include "display.h"
#include "panel.h"

#define SEG_A 0x01
#define SEG_B 0x02
#define SEG_C 0x04
#define SEG_D 0x08
#define SEG_E 0x10
#define SEG_F 0x20
#define SEG_G 0x40
#define SEG_DP 0x80

static const uint8_t digit_address[4] = {2, 0, 6, 4};

static const struct { uint8_t address, bit; } leds[LED_COUNT] = {
    [LED_PRESET] = {8, 7},       [LED_PERF] = {10, 7},      [LED_SETTINGS] = {12, 7}, [LED_TUNE] = {14, 7},
    [LED_SYNC_SOURCE] = {9, 0},  [LED_SYNC_CLOCK] = {13, 0}, [LED_REC] = {15, 0},
    [LED_SEQ1] = {11, 0},        [LED_SEQ2] = {11, 1},
    [LED_ARP_UP] = {13, 1},      [LED_ARP_ASSIGN] = {15, 1}, [LED_POWER] = {9, 1},
};

static uint8_t ram[PANEL_RAM_BYTES];
static uint8_t digits[4];
static int dirty = 1;

static uint8_t glyph(char c)
{
    static const uint8_t numbers[10] = {0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F};
    if (c >= '0' && c <= '9')
        return numbers[c - '0'];
    if (c >= 'a' && c <= 'z')
        c = (char)(c - 'a' + 'A');
    switch (c) {
    case 'A': return 0x77;
    case 'B': return 0x7C;
    case 'C': return 0x39;
    case 'D': return 0x5E;
    case 'E': return 0x79;
    case 'F': return 0x71;
    case 'G': return 0x3D;
    case 'H': return 0x76;
    case 'I': return 0x30;
    case 'J': return 0x1E;
    case 'L': return 0x38;
    case 'N': return 0x54;
    case 'O': return 0x5C;
    case 'P': return 0x73;
    case 'R': return 0x50;
    case 'S': return 0x6D;
    case 'T': return 0x78;
    case 'U': return 0x3E;
    case 'Y': return 0x6E;
    case '-': return SEG_G;
    case '_': return SEG_D;
    default: return 0;
    }
}

static void place(int position, uint8_t segments)
{
    uint8_t lo = digit_address[position], hi = (uint8_t)(lo + 1);
    ram[lo] = (uint8_t)((ram[lo] & ~0x07) | (segments & SEG_A ? 1 : 0) | (segments & SEG_B ? 2 : 0) | (segments & SEG_G ? 4 : 0));
    ram[hi] = (uint8_t)((ram[hi] & ~0xF8) | (segments & SEG_F ? 0x08 : 0) | (segments & SEG_E ? 0x10 : 0)
                        | (segments & SEG_D ? 0x20 : 0) | (segments & SEG_C ? 0x40 : 0) | (segments & SEG_DP ? 0x80 : 0));
}

static void set_digits(const uint8_t segments[4])
{
    for (int i = 0; i < 4; i++)
        if (segments[i] != digits[i]) {
            digits[i] = segments[i];
            place(i, segments[i]);
            dirty = 1;
        }
}

void display_clear(void)
{
    static const uint8_t blank[4] = {0};
    set_digits(blank);
}

void display_text(const char *text)
{
    uint8_t segments[4] = {0};
    int n = 0;
    for (; *text && n <= 4; text++) {
        if (*text == '.') {
            if (n > 0)
                segments[n - 1] |= SEG_DP;
            continue;
        }
        if (n == 4)
            break;
        segments[n++] = glyph(*text);
    }
    set_digits(segments);
}

void display_number(int value)
{
    char text[5] = "    ";
    int negative = value < 0, v = negative ? -value : value;
    if (v > 9999 || (negative && v > 999)) {
        display_text("----");
        return;
    }
    int i = 3;
    do {
        text[i--] = (char)('0' + v % 10);
        v /= 10;
    } while (v && i >= 0);
    if (negative && i >= 0)
        text[i] = '-';
    display_text(text);
}

void display_led(led_t led, int on)
{
    uint8_t *byte = &ram[leds[led].address], mask = (uint8_t)(1u << leds[led].bit);
    uint8_t next = on ? (uint8_t)(*byte | mask) : (uint8_t)(*byte & ~mask);
    if (next != *byte) {
        *byte = next;
        dirty = 1;
    }
}

void display_update(void)
{
    if (!dirty)
        return;
    panel_write(ram);
    dirty = 0;
}

void display_redraw(void)
{
    dirty = 1;
    display_update();
}
