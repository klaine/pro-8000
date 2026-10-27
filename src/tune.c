// Each edge of TUNE_CPR (PB8, EXTI8) is timestamped with the cycle counter in the EXTI9_5 interrupt, which runs at
// the highest priority, so the latency stays the same from edge to edge; the main loop meanwhile keeps the S&H
// refreshed, as a measurement can take longer than the hold capacitors keep their charge.
//
// Measured on an oscillator at 64 Hz: falling edges time single periods to about 1 cent, rising edges jitter by
// several cents (they come from the slow ramp), and both now and then get a stray edge mid-period or a burst of
// chatter. Hence falling edges by default, and the median-based span below instead of first-to-last.
#include "tune.h"
#include "clock.h"
#include "gpio.h"
#include "regs.h"
#include "sh.h"

#define TUNE_PIN 8
#define EXTI9_5_IRQ 23

static volatile uint32_t stamps[TUNE_MAX_PERIODS + 1];
static volatile uint32_t edges_wanted, edges_seen;
static uint32_t intervals[TUNE_MAX_PERIODS];

void EXTI9_5_IRQHandler(void)
{
    uint32_t now = DWT_CYCCNT;
    EXTI_PR = 1u << TUNE_PIN;
    if (edges_seen < edges_wanted)
        stamps[edges_seen++] = now;
}

void tune_init(void)
{
    RCC_AHB1ENR |= RCC_AHB1ENR_GPIOB;
    RCC_APB2ENR |= RCC_APB2ENR_SYSCFG;
    gpio_set_mode(GPIOB_BASE, 1u << TUNE_PIN, 0); // input; R111 pulls the LM311's open collector up
    SYSCFG_EXTICR(TUNE_PIN / 4) = (SYSCFG_EXTICR(TUNE_PIN / 4) & ~(0xFu << 4 * (TUNE_PIN % 4)))
                                | 1u << 4 * (TUNE_PIN % 4); // port B
    EXTI_PR = 1u << TUNE_PIN;
    NVIC_IPR(EXTI9_5_IRQ) = 0x00; // above SysTick and USB
}

static uint32_t median(uint32_t *values, uint32_t n)
{
    for (uint32_t i = 1; i < n; i++) // insertion sort: n is at most 255
        for (uint32_t j = i; j > 0 && values[j - 1] > values[j]; j--) {
            uint32_t t = values[j];
            values[j] = values[j - 1];
            values[j - 1] = t;
        }
    return values[n / 2];
}

tune_result_t tune_measure(uint32_t max_periods, uint32_t timeout_ms, int rising)
{
    tune_result_t result = {0, 0};
    if (max_periods > TUNE_MAX_PERIODS)
        max_periods = TUNE_MAX_PERIODS;
    if (rising) {
        EXTI_FTSR &= ~(1u << TUNE_PIN);
        EXTI_RTSR |= 1u << TUNE_PIN;
    } else {
        EXTI_RTSR &= ~(1u << TUNE_PIN);
        EXTI_FTSR |= 1u << TUNE_PIN;
    }
    edges_seen = 0;
    edges_wanted = max_periods + 1;
    EXTI_PR = 1u << TUNE_PIN;
    EXTI_IMR |= 1u << TUNE_PIN;
    NVIC_ISER(EXTI9_5_IRQ / 32) = 1u << (EXTI9_5_IRQ % 32);

    uint32_t start = DWT_CYCCNT;
    uint32_t limit = timeout_ms * (system_hz / 1000u);
    while (edges_seen < edges_wanted && DWT_CYCCNT - start < limit) {
        IWDG_KR = IWDG_KR_REFRESH;
        sh_refresh();
    }
    EXTI_IMR &= ~(1u << TUNE_PIN);
    NVIC_ICER(EXTI9_5_IRQ / 32) = 1u << (EXTI9_5_IRQ % 32);

    uint32_t n = edges_seen;
    if (n < 4)
        return result;
    for (uint32_t i = 0; i + 1 < n; i++)
        intervals[i] = stamps[i + 1] - stamps[i];
    uint32_t period = median(intervals, n - 1); // sorts `intervals`, so they're recomputed below

    uint32_t tolerance = period / 16, first = n, last = 0;
    for (uint32_t i = 0; i + 1 < n; i++) {
        uint32_t d = stamps[i + 1] - stamps[i];
        if (d + tolerance >= period && d <= period + tolerance) {
            if (first == n)
                first = i;
            last = i + 1;
        }
    }
    if (first == n)
        return result;
    result.cycles = stamps[last] - stamps[first];
    result.periods = (result.cycles + period / 2) / period;
    if (result.periods == 0)
        result.cycles = 0;
    return result;
}
