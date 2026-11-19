#include "avatar_skin.h"
#include <hal/board/stackchan_display.h>
#include <settings.h>

using namespace setup_workers;
using namespace uitk::lvgl_cpp;

AvatarSkinWorker::AvatarSkinWorker()
{
    panel_ = std::make_unique<Container>(lv_screen_active());
    panel_->setSize(320, 240);
    panel_->setAlign(LV_ALIGN_CENTER);
    panel_->setBgColor(lv_color_hex(0xEDF4FF));
    panel_->setBorderWidth(0);
    panel_->setRadius(0);
    panel_->removeFlag(LV_OBJ_FLAG_SCROLLABLE);
    title_ = std::make_unique<Label>(panel_->get());
    title_->setText("Avatar skin");
    title_->setTextColor(lv_color_hex(0x26206A));
    title_->align(LV_ALIGN_TOP_MID, 0, 10);
    const bool selected = StackChanAvatarDisplay::WhiteEyesSelected();
    auto make_button = [&](std::unique_ptr<Button>& button, const char* text, int y) {
        button = std::make_unique<Button>(panel_->get());
        apply_button_common_style(*button);
        button->setSize(270, 44);
        button->align(LV_ALIGN_TOP_MID, 0, y);
        button->label().setText(text);
    };
    make_button(white_, selected ? "White eyes (selected)" : "White eyes", 48);
    make_button(original_, selected ? "Original Grok Bot" : "Original Grok Bot (selected)", 100);
    make_button(back_, "Back", 160);
    // Event callbacks run on LVGL's task. Defer NVS access to update(),
    // which the setup application calls from its internal-RAM main task.
    white_->onClick().connect([this]() { desired_ = 1; });
    original_->onClick().connect([this]() { desired_ = 0; });
    back_->onClick().connect([this]() { _is_done = true; });
}

void AvatarSkinWorker::update()
{
    if (desired_ < 0) return;
    const bool white = desired_ != 0;
    desired_ = -1;
    { Settings settings("avatar", true); settings.SetBool("white_eyes", white); }
    white_->label().setText(white ? "White eyes (selected)" : "White eyes");
    original_->label().setText(white ? "Original Grok Bot" : "Original Grok Bot (selected)");
}
