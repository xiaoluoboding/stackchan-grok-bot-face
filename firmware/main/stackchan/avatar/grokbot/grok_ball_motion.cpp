#include "grok_ball_motion.h"
#include "grok_ball_data.h"

#include <algorithm>
#include <cstring>
#include <iterator>

namespace stackchan::avatar::grok_ball {
namespace {
constexpr int kSize = 200;
constexpr int kSubpixels = 4;
constexpr int kSampleStep = 256 / kSubpixels;
constexpr int kPointCount = 2 * kPoints;
constexpr int kSubrows = kSize * kSubpixels;

struct Point {
    int x;
    int y;
};

struct Edge {
    int end;
    // Q16 accumulators bound rounding error across an entire edge.
    int x;
    int step;
    int next;
    int eye;
};

int unpack_coordinate(const uint8_t*& data, int previous)
{
    const int code = *data++;
    if (code != 128) return previous + (code < 128 ? code : code - 256);
    const int coordinate = data[0] | (data[1] << 8);
    data += 2;
    return coordinate;
}

int sample_index(int q8)
{
    return std::clamp((q8 + kSampleStep / 2 - 1) / kSampleStep, 0, kSubrows);
}

// Add per-pixel sample counts for a horizontal span. Intervening full pixels
// are a single range addition rather than a loop over the eye interior.
void add_span(int8_t* changes, int begin, int end)
{
    if (begin >= end) return;
    const int first = begin / kSubpixels;
    const int last = (end - 1) / kSubpixels;
    if (first == last) {
        changes[first] += end - begin;
        changes[first + 1] -= end - begin;
        return;
    }
    changes[first] += kSubpixels - begin % kSubpixels;
    changes[first + 1] += begin % kSubpixels;
    const int end_coverage = (end - 1) % kSubpixels + 1;
    changes[last] -= kSubpixels - end_coverage;
    changes[last + 1] -= end_coverage;
}
}

AlphaBounds render(int state, uint32_t elapsed_ms, uint8_t* packed, int width, int height,
                   bool voice_active)
{
    if (!valid_state(state) || !packed || width != kSize || height != kSize) return {};
    const auto* match = std::find_if(std::begin(kStates), std::end(kStates),
                                     [state](const State& clip) { return clip.id == state; });
    if (match == std::end(kStates)) return {};
    const auto& clip = *match;
    const uint32_t time = clip.loop ? elapsed_ms % clip.period_ms : std::min(elapsed_ms, uint32_t(clip.period_ms));
    const uint32_t position = time * kFrames * 256 / clip.period_ms;
    const int frame = std::min(int(position >> 8), kFrames - 1);
    const int next = clip.loop ? (frame + 1) % kFrames : std::min(frame + 1, kFrames - 1);
    const int mix = position & 255;
    const auto* a = clip.frames + clip.offsets[frame];
    const auto* b = clip.frames + clip.offsets[next];
    Point points[kPointCount];
    for (int eye = 0; eye < 2; ++eye) {
        Point first{};
        Point second{};
        for (int i = 0; i < kPoints; ++i) {
            first.x = unpack_coordinate(a, first.x);
            first.y = unpack_coordinate(a, first.y);
            second.x = unpack_coordinate(b, second.x);
            second.y = unpack_coordinate(b, second.y);
            // Convert Q4 keyframe coordinates to interpolated Q8.
            points[eye * kPoints + i] = {
                (first.x * 256 + (second.x - first.x) * mix + 8) / 16,
                (first.y * 256 + (second.y - first.y) * mix + 8) / 16,
            };
        }
    }

    // The completion pose uses the largest fill by far. During audio its
    // smaller footprint reduces LCD flush traffic while keeping the eye shape.
    if (voice_active && state == 33) {
        constexpr int center = kSize * 128;
        for (auto& point : points) {
            point.x = center + (point.x - center) * 3 / 4;
            point.y = center + (point.y - center) * 3 / 4;
        }
    }

    int8_t starts[kSubrows];
    std::memset(starts, -1, sizeof(starts));
    Edge edges[kPointCount];
    int count = 0;
    int first_row = kSize;
    int end_row = 0;
    for (int eye = 0; eye < 2; ++eye) {
        const auto* contour = points + eye * kPoints;
        for (int p = 0, prev = kPoints - 1; p < kPoints; prev = p++) {
            auto low = contour[prev];
            auto high = contour[p];
            if (low.y > high.y) std::swap(low, high);
            const int start = sample_index(low.y);
            const int end = sample_index(high.y);
            if (start == end) continue;
            const int dx = high.x - low.x;
            const int dy = high.y - low.y;
            const int step = dx * kSampleStep * 256 / dy;
            const int offset = start * kSampleStep + kSampleStep / 2 - low.y;
            const int x = low.x * 256 + dx * offset * 256 / dy;
            edges[count] = {end, x, step, starts[start], eye};
            starts[start] = count++;
            first_row = std::min(first_row, start / kSubpixels);
            end_row = std::max(end_row, (end + kSubpixels - 1) / kSubpixels);
        }
    }

    std::memset(packed, 0, kSize * kSize / 2);
    AlphaBounds bounds{kSize, kSize, -1, -1};
    int active = -1;
    for (int y = first_row; y < end_row; ++y) {
        int8_t changes[kSize + 1] = {};
        int left = kSize;
        int right = 0;
        for (int sub = 0; sub < kSubpixels; ++sub) {
            const int row = y * kSubpixels + sub;
            for (int edge = starts[row]; edge != -1;) {
                const int next_edge = edges[edge].next;
                edges[edge].next = active;
                active = edge;
                edge = next_edge;
            }

            // Both contours can overlap. Merge their even-odd fills before
            // counting coverage, so an overlap never exceeds 16 samples.
            struct Crossing { int x; int eye; };
            Crossing crossings[kPointCount];
            int crossings_count = 0;
            for (int* link = &active; *link != -1;) {
                auto& edge = edges[*link];
                if (row >= edge.end) {
                    *link = edge.next;
                    continue;
                }
                const int x = sample_index((edge.x + 128) / 256);
                int at = crossings_count++;
                while (at > 0 && crossings[at - 1].x > x) {
                    crossings[at] = crossings[at - 1];
                    --at;
                }
                crossings[at] = {x, edge.eye};
                edge.x += edge.step;
                link = &edge.next;
            }
            int inside = 0;
            int begin = 0;
            for (int i = 0; i < crossings_count; ++i) {
                const auto& crossing = crossings[i];
                if (inside == 0) begin = crossing.x;
                inside ^= 1 << crossing.eye;
                if (inside == 0 && begin < crossing.x) {
                    add_span(changes, begin, crossing.x);
                    left = std::min(left, begin / kSubpixels);
                    right = std::max(right, (crossing.x + kSubpixels - 1) / kSubpixels);
                }
            }
        }

        if (left >= right) continue;
        bounds.x1 = std::min(bounds.x1, left);
        bounds.x2 = std::max(bounds.x2, right - 1);
        bounds.y1 = std::min(bounds.y1, y);
        bounds.y2 = y;
        // Pack each byte in internal RAM, then write the active span to the
        // shared PSRAM image without a read/modify/write per pixel.
        uint8_t row[kSize / 2];
        const int first = left & ~1;
        const int last = (right + 1) & ~1;
        int coverage = 0;
        for (int x = first; x < last; x += 2) {
            coverage += changes[x];
            const int high = (coverage * 15 + 8) >> 4;
            coverage += changes[x + 1];
            const int low = (coverage * 15 + 8) >> 4;
            row[(x - first) / 2] = (high << 4) | low;
        }
        std::memcpy(packed + (y * kSize + first) / 2, row, (last - first) / 2);
    }
    return bounds;
}
}
