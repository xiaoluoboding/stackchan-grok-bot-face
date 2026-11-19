#pragma once
#include "workers.h"

namespace setup_workers {
class AvatarSkinWorker : public WorkerBase {
public:
    AvatarSkinWorker();
    void update() override;
private:
    int desired_ = -1;
    std::unique_ptr<uitk::lvgl_cpp::Container> panel_;
    std::unique_ptr<uitk::lvgl_cpp::Label> title_;
    std::unique_ptr<uitk::lvgl_cpp::Button> white_, original_, back_;
};
}
