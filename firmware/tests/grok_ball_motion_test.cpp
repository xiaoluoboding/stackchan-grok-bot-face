#include <stackchan/avatar/grokbot/grok_ball_motion.h>
#include <stackchan/avatar/grokbot/grok_ball_data.h>

#include <array>
#include <cassert>

using namespace stackchan::avatar;

namespace {
constexpr int kSize = 200;
using Frame = std::array<uint8_t, kSize * kSize / 2>;

bool has_alpha_edge(const Frame& frame)
{
    for (const auto byte : frame) {
        const int high = byte >> 4;
        const int low = byte & 15;
        if ((high > 0 && high < 15) || (low > 0 && low < 15)) return true;
    }
    return false;
}

AlphaBounds check_frame(int id, uint32_t time, bool voice_active, Frame& frame)
{
    const auto bounds = grok_ball::render(id, time, frame.data(), kSize, kSize, voice_active);
    const auto pixels = packed_alpha_bounds(frame.data(), kSize, kSize);
    assert(bounds.valid());
    assert(bounds.x1 > 0 && bounds.y1 > 0 && bounds.x2 < kSize - 1 && bounds.y2 < kSize - 1);
    assert(bounds.x1 == pixels.x1 && bounds.y1 == pixels.y1 && bounds.x2 == pixels.x2 && bounds.y2 == pixels.y2);
    assert(has_alpha_edge(frame));
    return bounds;
}
}

int main()
{
    Frame frame{};
    assert(!grok_ball::valid_state(38) && !grok_ball::valid_state(39));
    static_assert(std::size(grok_ball::kStates) == 10);
    for (const auto& state : grok_ball::kStates) {
        const int id = state.id;
        const auto duration = state.period_ms;
        for (uint32_t time = 0; time <= duration + 100U; time += 17) {
            const auto full = check_frame(id, time, false, frame);
            if (id == 33) {
                const auto compact = check_frame(id, time, true, frame);
                assert(compact.x2 - compact.x1 < full.x2 - full.x1);
                assert(compact.y2 - compact.y1 < full.y2 - full.y1);
            }
        }
    }
}
