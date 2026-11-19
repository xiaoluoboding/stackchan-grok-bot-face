#pragma once

#include "alpha_bounds.h"

namespace stackchan::avatar::grok_ball {
inline constexpr int kReplyState = 37;
inline constexpr int kSearchState = 40;
inline constexpr bool valid_state(int state)
{
    return (state >= 30 && state <= 37) || state == 40 || state == 41;
}

// Packed I4, high nibble first. Return the footprint for LVGL's dirty region.
AlphaBounds render(int state, uint32_t elapsed_ms, uint8_t* packed, int width, int height,
                   bool voice_active = false);
}
