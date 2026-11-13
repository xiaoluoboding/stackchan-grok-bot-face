/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "grokbot_avatar.h"

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <miniz.h>
#include <src/misc/cache/lv_cache.h>

#include <cstdlib>
#include <cstring>

namespace stackchan::avatar {
namespace {

constexpr const char* TAG = "GrokBotAvatar";

const char* const kIdleClips[] = {
    "idle", "waiting", "humming", "bored", "drowsy", "waking", "curious", "shy",
};

const char* const kListeningClips[] = {"listening", "curious", "shy", "humming"};
const char* const kSpeakingClips[]  = {"streaming", "sending", "notifying", "happy", "dictating"};
const char* const kWorkingClips[] = {"working", "reading", "coding", "running", "computing", "controlling", "writing"};
const char* const kLoadingClips[] = {
    "loading", "progress", "searching", "thinking", "orbit", "radar", "spawning", "receiving", "uploading",
};
const char* const kCelebratingClips[] = {"celebrate", "finished", "approving", "excited", "laughing"};
const char* const kErrorClips[]       = {"failed", "blocked", "sad"};
const char* const kHappyClips[]       = {"happy", "excited", "laughing", "proud", "celebrate", "approving", "playful"};
const char* const kAngryClips[]       = {"angry", "blocked", "suspicious"};
const char* const kSadClips[]         = {"sad", "failed", "blocked"};
const char* const kDoubtClips[]       = {"confused", "curious", "suspicious", "thinking"};
const char* const kSleepClips[]       = {"drowsy", "sleeping", "bored"};

template <size_t N>
constexpr size_t count_of(const char* const (&)[N])
{
    return N;
}

void* alloc_frame_buffer(size_t size)
{
    // Wi-Fi, audio and LVGL draw buffers require scarce internal RAM. Keep the
    // animation working buffers in PSRAM and fall back to internal RAM only if
    // PSRAM allocation fails.
    void* ptr = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (ptr == nullptr) {
        ptr = heap_caps_malloc(size, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    return ptr;
}

}  // namespace

bool GrokBotAvatar::init(lv_obj_t* parent, const uint8_t* atlas, size_t atlas_size, int screen_width, int screen_height,
                         const lv_font_t* font)
{
    if (parent == nullptr || atlas == nullptr || atlas_size == 0) {
        ESP_LOGE(TAG, "Invalid init arguments");
        return false;
    }
    if (!player_.init(atlas, atlas_size)) {
        return false;
    }

    if (player_.width() == 0 || player_.height() == 0 || (player_.width() & 1) != 0 || player_.fps() == 0) {
        ESP_LOGE(TAG, "Unsupported atlas metadata");
        return false;
    }

    const size_t packed_size = static_cast<size_t>(player_.width()) * player_.height() / 2;
    constexpr size_t kPaletteSize = 16 * sizeof(lv_color32_t);
    if (player_.packed_frame_size() != packed_size) {
        ESP_LOGE(TAG, "Unsupported atlas frame size");
        return false;
    }

    frame_buffer_ = static_cast<uint8_t*>(alloc_frame_buffer(kPaletteSize + packed_size));
    delta_        = static_cast<uint8_t*>(alloc_frame_buffer(packed_size));
    if (frame_buffer_ == nullptr || delta_ == nullptr) {
        ESP_LOGE(TAG, "Frame buffer allocation failed");
        heap_caps_free(frame_buffer_);
        heap_caps_free(delta_);
        frame_buffer_ = nullptr;
        delta_        = nullptr;
        return false;
    }
    packed_ = frame_buffer_ + kPaletteSize;

    for (int i = 0; i < 16; ++i) {
        const uint8_t v = static_cast<uint8_t>(i * 17);
        lv_color32_t color = {};
        color.blue         = v;
        color.green        = v;
        color.red          = v;
        color.alpha        = 0xFF;
        memcpy(frame_buffer_ + static_cast<size_t>(i) * sizeof(color), &color, sizeof(color));
    }

    panel_ = lv_obj_create(parent);
    lv_obj_set_size(panel_, screen_width, screen_height);
    lv_obj_align(panel_, LV_ALIGN_CENTER, 0, 0);
    lv_obj_set_style_radius(panel_, 0, 0);
    lv_obj_set_style_border_width(panel_, 0, 0);
    lv_obj_set_style_pad_all(panel_, 0, 0);
    lv_obj_set_style_bg_color(panel_, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(panel_, LV_OPA_COVER, 0);
    lv_obj_remove_flag(panel_, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(panel_, LV_OBJ_FLAG_CLICKABLE);

    image_dsc_.header.magic  = LV_IMAGE_HEADER_MAGIC;
    image_dsc_.header.cf     = LV_COLOR_FORMAT_I4;
    image_dsc_.header.w      = player_.width();
    image_dsc_.header.h      = player_.height();
    image_dsc_.header.stride = player_.width() / 2;
    image_dsc_.data_size     = kPaletteSize + packed_size;
    image_dsc_.data          = frame_buffer_;

    image_ = lv_image_create(panel_);
    lv_obj_set_size(image_, player_.width(), player_.height());
    lv_obj_align(image_, LV_ALIGN_CENTER, 0, 0);
    lv_obj_remove_flag(image_, LV_OBJ_FLAG_SCROLLABLE);
    lv_image_set_src(image_, &image_dsc_);

    _key_elements.leftEye      = std::make_unique<Feature>();
    _key_elements.rightEye     = std::make_unique<Feature>();
    _key_elements.mouth        = std::make_unique<Feature>();
    _key_elements.speechBubble = std::make_unique<DefaultSpeechBubble>(panel_, lv_color_white(), lv_color_black(),
                                                                       font != nullptr ? font : &lv_font_montserrat_16);

    const int64_t now = esp_timer_get_time();
    next_frame_us_    = now;
    next_log_us_      = now + 1000000;
    mode_             = Mode::Idle;
    _emotion          = Emotion::Neutral;
    emotion_override_ = false;
    playback_fps_     = player_.fps();
    ready_            = true;
    if (!select_clip("idle")) {
        choose_clip_for_mode();
    }

    ESP_LOGI(TAG, "Atlas ready: %dx%d, %d clips, %d fps, indexed=%u delta=%u bytes", player_.width(),
             player_.height(), player_.clip_count(), player_.fps(), static_cast<unsigned>(kPaletteSize + packed_size),
             static_cast<unsigned>(packed_size));
    return true;
}

GrokBotAvatar::~GrokBotAvatar()
{
    ready_ = false;
    _key_elements.speechBubble.reset();
    _key_elements.mouth.reset();
    _key_elements.rightEye.reset();
    _key_elements.leftEye.reset();

    if (image_ != nullptr) {
        lv_obj_del(image_);
        image_ = nullptr;
    }
    if (panel_ != nullptr) {
        lv_obj_del(panel_);
        panel_ = nullptr;
    }

    heap_caps_free(frame_buffer_);
    heap_caps_free(delta_);
    frame_buffer_ = nullptr;
    packed_       = nullptr;
    delta_        = nullptr;
}

void GrokBotAvatar::update()
{
    Avatar::update();
    if (!ready_ || image_ == nullptr) {
        return;
    }
    if (suspended_) {
        return;
    }

    const int64_t now = esp_timer_get_time();
    if (now < next_frame_us_) {
        if (now >= next_log_us_) {
            log_frame_stats(now);
        }
        return;
    }

    const int64_t decode_start = now;
    bool rendered              = false;

    if (rebuild_pending_) {
        rendered = rebuild_to_current();
    } else if (!reverse_) {
        rendered = render_current_frame();
        if (rendered) {
            if (player_.current_index() + 1 >= player_.selected_frame_count()) {
                reverse_ = true;
            } else {
                player_.advance();
            }
        }
    } else if (player_.current_index() <= player_.start_frame()) {
        reverse_ = false;
        play_next_clip();
    } else {
        rendered = render_backward_frame();
    }

    if (rendered) {
        ++frames_since_log_;
        decode_us_since_log_ += esp_timer_get_time() - decode_start;
    }

    const int64_t frame_us = 1000000LL / playback_fps_;
    next_frame_us_ += frame_us;
    if (next_frame_us_ < now - frame_us) {
        next_frame_us_ = now + frame_us;
    }

    if (now >= next_log_us_) {
        log_frame_stats(now);
    }
}

void GrokBotAvatar::setSuspended(bool suspended)
{
    if (suspended_ == suspended) {
        return;
    }
    suspended_ = suspended;
    if (!suspended_) {
        next_frame_us_ = esp_timer_get_time();
    }
    ESP_LOGI(TAG, "animation suspended=%d", static_cast<int>(suspended));
}

void GrokBotAvatar::setMode(Mode mode)
{
    if (!ready_ || (mode_ == mode && !emotion_override_)) {
        return;
    }
    mode_             = mode;
    emotion_override_ = false;
    // Direct PSRAM LCD DMA competes with audio processing for memory bandwidth.
    // Cap voice states so the microphone and playback keep their deadlines.
    playback_fps_ = (mode_ == Mode::Listening || mode_ == Mode::Speaking) ? 12 : player_.fps();
    next_frame_us_ = esp_timer_get_time();
    if (mode_ == Mode::Idle) {
        _emotion = Emotion::Neutral;
    }
    choose_clip_for_mode();
    ESP_LOGI(TAG, "mode -> %d, clip=%s, target=%d fps", static_cast<int>(mode_), currentClipName(), playback_fps_);
}

void GrokBotAvatar::setEmotion(const Emotion& emotion)
{
    if (!ready_) {
        return;
    }

    if (emotion == Emotion::Neutral) {
        if (!emotion_override_) {
            return;
        }
        emotion_override_ = false;
        _emotion          = Emotion::Neutral;
        choose_clip_for_mode();
        ESP_LOGI(TAG, "emotion cleared, clip=%s", currentClipName());
        return;
    }

    if (emotion_override_ && _emotion == emotion) {
        return;
    }

    _emotion          = emotion;
    emotion_override_ = true;
    choose_clip_for_mode();
    ESP_LOGI(TAG, "emotion -> %d, clip=%s", static_cast<int>(emotion), currentClipName());
}

const char* GrokBotAvatar::currentClipName() const
{
    return player_.selected_index() >= 0 ? player_.selected_name() : "";
}

bool GrokBotAvatar::decode_frame(const GrokBotPlayer::FrameInfo& frame)
{
    if (frame.data == nullptr || frame.compressed_size == 0 || packed_ == nullptr || delta_ == nullptr) {
        return false;
    }

    const size_t packed_size = player_.packed_frame_size();
    const size_t unpacked    = tinfl_decompress_mem_to_mem(delta_, packed_size, frame.data, frame.compressed_size,
                                                           TINFL_FLAG_PARSE_ZLIB_HEADER);
    if (unpacked != packed_size) {
        ESP_LOGE(TAG, "inflate failed: %u/%u", static_cast<unsigned>(unpacked), static_cast<unsigned>(packed_size));
        return false;
    }

    for (size_t i = 0; i < packed_size; ++i) {
        packed_[i] = frame.delta ? (packed_[i] ^ delta_[i]) : delta_[i];
    }
    return true;
}

bool GrokBotAvatar::decode_backward_frame(const GrokBotPlayer::FrameInfo& frame)
{
    if (!frame.delta || frame.data == nullptr || frame.compressed_size == 0 || packed_ == nullptr ||
        delta_ == nullptr) {
        return false;
    }

    const size_t packed_size = player_.packed_frame_size();
    const size_t unpacked    = tinfl_decompress_mem_to_mem(delta_, packed_size, frame.data, frame.compressed_size,
                                                           TINFL_FLAG_PARSE_ZLIB_HEADER);
    if (unpacked != packed_size) {
        return false;
    }

    for (size_t i = 0; i < packed_size; ++i) {
        packed_[i] ^= delta_[i];
    }
    return true;
}

bool GrokBotAvatar::rebuild_to_current()
{
    const int target = player_.current_index();
    if (!player_.seek(0)) {
        return false;
    }
    for (int i = 0; i <= target; ++i) {
        GrokBotPlayer::FrameInfo frame = {};
        if (!player_.frame_info(i, &frame) || !decode_frame(frame)) {
            return false;
        }
    }
    if (!player_.seek(target)) {
        return false;
    }
    rebuild_pending_ = false;
    invalidate_image();
    if (target + 1 < player_.selected_frame_count()) {
        player_.advance();
    } else {
        reverse_ = true;
    }
    return true;
}

bool GrokBotAvatar::render_current_frame()
{
    GrokBotPlayer::FrameInfo frame = {};
    if (!player_.current_frame(&frame) || !decode_frame(frame)) {
        return false;
    }
    invalidate_image();
    return true;
}

bool GrokBotAvatar::render_backward_frame()
{
    GrokBotPlayer::FrameInfo frame = {};
    if (!player_.current_frame(&frame) || !decode_backward_frame(frame) || !player_.retreat()) {
        return false;
    }
    invalidate_image();
    return true;
}

void GrokBotAvatar::invalidate_image()
{
    if (image_ != nullptr) {
        lv_obj_invalidate(image_);
    }
}

bool GrokBotAvatar::select_clip(const char* name)
{
    if (name == nullptr || name[0] == '\0') {
        return false;
    }
    for (int i = 0; i < player_.clip_count(); ++i) {
        if (strcmp(player_.clip_name(i), name) == 0) {
            if (!player_.select(i) || !player_.seek(player_.start_frame())) {
                return false;
            }
            rebuild_pending_ = true;
            reverse_         = false;
            return true;
        }
    }
    ESP_LOGW(TAG, "Atlas clip not found: %s", name);
    return false;
}

void GrokBotAvatar::play_next_clip()
{
    if (!choose_clip_for_mode()) {
        rebuild_pending_ = true;
    }
}

bool GrokBotAvatar::choose_clip_for_mode()
{
    const char* next = emotion_override_ ? choose_emotion_clip() : nullptr;
    if (next != nullptr) {
        return select_clip(next);
    }

    switch (mode_) {
        case Mode::Listening:
            next = pick_random(kListeningClips, count_of(kListeningClips), currentClipName());
            break;
        case Mode::Speaking:
            next = pick_random(kSpeakingClips, count_of(kSpeakingClips), currentClipName());
            break;
        case Mode::Working:
            next = pick_random(kWorkingClips, count_of(kWorkingClips), currentClipName());
            break;
        case Mode::Loading:
            next = pick_random(kLoadingClips, count_of(kLoadingClips), currentClipName());
            break;
        case Mode::Celebrating:
            next = pick_random(kCelebratingClips, count_of(kCelebratingClips), currentClipName());
            break;
        case Mode::Error:
            next = pick_random(kErrorClips, count_of(kErrorClips), currentClipName());
            break;
        case Mode::Sleep:
            next = pick_random(kSleepClips, count_of(kSleepClips), currentClipName());
            break;
        case Mode::Emotion:
            next = choose_emotion_clip();
            break;
        case Mode::Idle:
        default:
            next = choose_idle_clip();
            break;
    }
    return select_clip(next);
}

const char* GrokBotAvatar::choose_idle_clip()
{
    return pick_random(kIdleClips, count_of(kIdleClips), currentClipName());
}

const char* GrokBotAvatar::choose_emotion_clip()
{
    switch (_emotion) {
        case Emotion::Happy:
            return pick_random(kHappyClips, count_of(kHappyClips), currentClipName());
        case Emotion::Angry:
            return pick_random(kAngryClips, count_of(kAngryClips), currentClipName());
        case Emotion::Sad:
            return pick_random(kSadClips, count_of(kSadClips), currentClipName());
        case Emotion::Doubt:
            return pick_random(kDoubtClips, count_of(kDoubtClips), currentClipName());
        case Emotion::Sleepy:
            return pick_random(kSleepClips, count_of(kSleepClips), currentClipName());
        case Emotion::Neutral:
        default:
            return choose_idle_clip();
    }
}

const char* GrokBotAvatar::pick_random(const char* const* pool, size_t count, const char* avoid)
{
    if (pool == nullptr || count == 0) {
        return nullptr;
    }
    size_t index = esp_random() % count;
    if (count > 1 && avoid != nullptr && strcmp(pool[index], avoid) == 0) {
        index = (index + 1 + (esp_random() % (count - 1))) % count;
    }
    return pool[index];
}

void GrokBotAvatar::log_frame_stats(int64_t now_us)
{
    const uint32_t frames       = frames_since_log_;
    const int64_t avg_decode_us = frames ? decode_us_since_log_ / frames : 0;
    ESP_LOGI(TAG, "fps=%lu clip=%s frame=%d/%d decode=%luus", static_cast<unsigned long>(frames), currentClipName(),
             player_.current_index(), player_.selected_frame_count(), static_cast<unsigned long>(avg_decode_us));
    frames_since_log_    = 0;
    decode_us_since_log_ = 0;
    next_log_us_         = now_us + 1000000;
}

}  // namespace stackchan::avatar
