#pragma once
#include <cstdint>

namespace stackchan::avatar {
struct AlphaBounds {
    int x1 = 0, y1 = 0, x2 = -1, y2 = -1;
    bool valid() const { return x1 <= x2 && y1 <= y2; }
};

// Indexed white eyes use the I4 index as alpha coverage; zero is transparent.
inline AlphaBounds packed_alpha_bounds(const uint8_t* pixels, int width, int height)
{
    AlphaBounds bounds{width, height, -1, -1};
    for (int y = 0; y < height; ++y) {
        for (int pair = 0; pair < width / 2; ++pair) {
            const uint8_t value = pixels[y * (width / 2) + pair];
            if (value == 0) continue;
            const int left = pair * 2 + ((value & 0xf0) ? 0 : 1);
            const int right = pair * 2 + ((value & 0x0f) ? 1 : 0);
            if (left < bounds.x1) bounds.x1 = left;
            if (right > bounds.x2) bounds.x2 = right;
            if (y < bounds.y1) bounds.y1 = y;
            bounds.y2 = y;
        }
    }
    return bounds;
}

inline AlphaBounds union_alpha_bounds(AlphaBounds previous, AlphaBounds current)
{
    if (!previous.valid()) return current;
    if (!current.valid()) return previous;
    return {previous.x1 < current.x1 ? previous.x1 : current.x1,
            previous.y1 < current.y1 ? previous.y1 : current.y1,
            previous.x2 > current.x2 ? previous.x2 : current.x2,
            previous.y2 > current.y2 ? previous.y2 : current.y2};
}
}
