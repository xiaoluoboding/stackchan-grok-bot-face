/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "../avatar/avatar.h"
#include "../skins/default/default.h"
#include "grokbot_player.h"

#include <cstddef>
#include <cstdint>
#include <lvgl.h>

namespace stackchan::avatar {

/**
 * @brief GrokBot atlas-backed avatar for the 320x240 StackChan display.
 *
 * The atlas is decoded one XOR delta at a time into a 4-bit grayscale image
 * with an LVGL palette. The display uses double buffering so rendering and SPI
 * transfer overlap while each frame remains fully coherent.
 */
class GrokBotAvatar : public Avatar {
public:
    enum class Mode {
        Idle,
        Listening,
        Speaking,
        Working,
        Loading,
        Celebrating,
        Error,
        Emotion,
        Sleep,
    };

    bool init(lv_obj_t* parent, const uint8_t* atlas, size_t atlas_size, int screen_width, int screen_height,
              const lv_font_t* font = &lv_font_montserrat_16);
    ~GrokBotAvatar() override;

    void update() override;
    void setEmotion(const Emotion& emotion) override;

    void setMode(Mode mode);
    void setIdle()
    {
        setMode(Mode::Idle);
    }
    void setListening()
    {
        setMode(Mode::Listening);
    }
    void setSpeaking()
    {
        setMode(Mode::Speaking);
    }
    void setWorking()
    {
        setMode(Mode::Working);
    }
    void setLoading()
    {
        setMode(Mode::Loading);
    }
    void setCelebrating()
    {
        setMode(Mode::Celebrating);
    }
    void setError()
    {
        setMode(Mode::Error);
    }
    void setSleep()
    {
        setMode(Mode::Sleep);
    }

    lv_obj_t* getPanel() const
    {
        return panel_;
    }
    void setSuspended(bool suspended);
    bool isSuspended() const { return suspended_; }
    const char* currentClipName() const;
    int clipCount() const
    {
        return player_.clip_count();
    }
    int fps() const
    {
        return player_.fps();
    }
    bool isReady() const
    {
        return ready_;
    }

private:
    bool decode_frame(const GrokBotPlayer::FrameInfo& frame);
    bool decode_backward_frame(const GrokBotPlayer::FrameInfo& frame);
    bool rebuild_to_current();
    bool render_current_frame();
    bool render_backward_frame();
    void invalidate_image();

    bool select_clip(const char* name);
    void play_next_clip();
    bool choose_clip_for_mode();
    const char* choose_idle_clip();
    const char* choose_emotion_clip();
    static const char* pick_random(const char* const* pool, size_t count, const char* avoid);

    void log_frame_stats(int64_t now_us);

    GrokBotPlayer player_;
    lv_obj_t* panel_          = nullptr;
    lv_obj_t* image_          = nullptr;
    lv_image_dsc_t image_dsc_ = {};
    uint8_t* frame_buffer_    = nullptr;
    uint8_t* packed_          = nullptr;
    uint8_t* delta_           = nullptr;

    Mode mode_                   = Mode::Idle;
    bool ready_                  = false;
    bool emotion_override_       = false;
    bool reverse_                = false;
    bool rebuild_pending_        = true;
    bool suspended_              = false;
    int playback_fps_            = 30;
    int64_t next_frame_us_       = 0;
    int64_t next_log_us_         = 0;
    uint32_t frames_since_log_   = 0;
    int64_t decode_us_since_log_ = 0;
};

}  // namespace stackchan::avatar
