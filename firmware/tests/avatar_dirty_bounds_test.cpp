#include "stackchan/avatar/grokbot/alpha_bounds.h"
#include <array>
#include <cassert>
#include <cstdio>
using namespace stackchan::avatar;
int main()
{
    constexpr int w = 32, h = 24;
    std::array<uint8_t, w*h/2> packed{};
    std::array<uint8_t, w*h> displayed{};
    AlphaBounds previous;
    // Simulate moving eyes, low-alpha edges, blinks and all four boundaries.
    // A partial repaint must produce exactly the same pixels as a full repaint.
    for (int frame=0; frame<300; ++frame) {
        packed.fill(0);
        std::array<uint8_t,w*h> expected{};
        if (frame % 7 != 0) {
            for (int i=0;i<12;++i) {
                const int x=(frame*3+i)%w, y=(frame+i*2)%h;
                expected[y*w+x]=1+(i%15);
            }
        }
        for (int i=0;i<w*h;++i) packed[i/2] |= expected[i] << ((i%2) ? 0 : 4);
        const auto current=packed_alpha_bounds(packed.data(),w,h);
        const auto dirty=union_alpha_bounds(previous,current);
        if (dirty.valid()) {
            assert(dirty.x1>=0 && dirty.y1>=0 && dirty.x2<w && dirty.y2<h);
            for (int y=dirty.y1;y<=dirty.y2;++y)
                for (int x=dirty.x1;x<=dirty.x2;++x) displayed[y*w+x]=expected[y*w+x];
        }
        assert(displayed==expected);
        previous=current;
    }
    packed.fill(0);packed[0]=0x10;
    auto b=packed_alpha_bounds(packed.data(),w,h);assert(b.x1==0 && b.x2==0 && b.y1==0 && b.y2==0);
    packed[0]=0x01;b=packed_alpha_bounds(packed.data(),w,h);assert(b.x1==1 && b.x2==1);
    std::puts("300 incremental frames match full repaint, including blinking and edge pixels");
}
