/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "grokbot_avatar.h"
#include "grok_ball_motion.h"
#include "../decorators/decorators.h"

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
                         const lv_font_t* font, bool transparent_eyes)
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

    setPalette(transparent_eyes);

    panel_ = lv_obj_create(parent);
    lv_obj_set_size(panel_, screen_width, screen_height);
    lv_obj_align(panel_, LV_ALIGN_CENTER, 0, 0);
    lv_obj_set_style_radius(panel_, 0, 0);
    lv_obj_set_style_border_width(panel_, 0, 0);
    lv_obj_set_style_pad_all(panel_, 0, 0);
    lv_obj_set_style_bg_color(panel_, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(panel_, transparent_eyes ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
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
                                                                       font != nullptr ? font : &lv_font_montserrat_16, false);

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

void GrokBotAvatar::setPalette(bool transparent_eyes)
{
    transparent_eyes_ = transparent_eyes;
    full_redraw_pending_ = true;
    previous_eyes_bounds_ = {};
    for (int i = 0; i < 16; ++i) {
        const uint8_t v = static_cast<uint8_t>(i * 17);
        lv_color32_t color = {};
        color.blue = color.green = color.red = transparent_eyes ? 255 : v;
        color.alpha = transparent_eyes ? v : 255;
        memcpy(frame_buffer_ + static_cast<size_t>(i) * sizeof(color), &color, sizeof(color));
    }
}

bool GrokBotAvatar::setAtlas(const uint8_t* atlas, size_t atlas_size, bool transparent_eyes)
{
    GrokBotPlayer candidate;
    if (!ready_ || !candidate.init(atlas, atlas_size) || candidate.width() != player_.width() ||
        candidate.height() != player_.height() || candidate.packed_frame_size() != player_.packed_frame_size()) {
        ESP_LOGE(TAG, "Skin atlas incompatible; keeping current avatar");
        return false;
    }
    // Drop converted indexed pixels before changing the palette or frame buffer.
    lv_image_cache_drop(&image_dsc_);
    player_ = candidate;
    setPalette(transparent_eyes);
    agent_until_us_ = 0;
    startAgentState(agentStateForMode(), esp_timer_get_time());
    memset(packed_, 0, player_.packed_frame_size());
    lv_obj_set_style_bg_opa(panel_, transparent_eyes ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
    playback_fps_ = (mode_ == Mode::Listening || mode_ == Mode::Speaking) ? 12 : player_.fps();
    rebuild_pending_ = true;
    reverse_ = false;
    next_frame_us_ = esp_timer_get_time();
    if (!choose_clip_for_mode()) return false;
    ESP_LOGI(TAG, "Skin switched: %s", transparent_eyes ? "white-eyes (alpha)" : "original");
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
    if (wake_shy_until_us_ != 0 && esp_timer_get_time() >= wake_shy_until_us_) {
        removeDecorator(wake_shy_decorator_id_);
        wake_shy_decorator_id_ = -1;
        wake_shy_until_us_ = 0;
        choose_clip_for_mode();
        next_frame_us_ = esp_timer_get_time();
        ESP_LOGI(TAG, "Wake shy finished; restored current avatar mode");
    }
    if (suspended_) {
        // Decode one pending skin frame on the normal LVGL update task,
        // even while TLS pauses playback. Decompression has a substantial
        // stack footprint and must not run in the app's NVS switch callback.
        if (rebuild_pending_ && full_redraw_pending_) rebuild_to_current();
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

    if (agent_until_us_ != 0 && now >= agent_until_us_) {
        agent_until_us_ = 0;
        startAgentState(agentStateForMode(), now);
        choose_clip_for_mode();
    }
    if (search_until_us_ != 0 && now >= search_until_us_) {
        search_until_us_ = 0;
        if (agent_state_ < 0) choose_clip_for_mode();
    }
    const int state = activeAgentState();
    if (state >= 0) {
        const bool voice_active = mode_ == Mode::Speaking || mode_ == Mode::Listening;
        const auto started = search_until_us_ > now ? search_started_us_ : agent_started_us_;
        const auto bounds = grok_ball::render(state, (now - started) / 1000,
                                              packed_, player_.width(), player_.height(), voice_active);
        rendered = bounds.valid();
        if (rendered) {
            invalidate_image(&bounds);
            rebuild_pending_ = true;
        }
    } else if (rebuild_pending_) {
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

int GrokBotAvatar::agentStateForMode() const
{
    if (!transparent_eyes_) return -1;
    switch (mode_) {
        case Mode::Listening: return 35;
        case Mode::Speaking: return grok_ball::kReplyState;
        case Mode::Working: return 32;
        case Mode::Loading: return 36;
        case Mode::Celebrating: return 33;
        case Mode::Error: return 34;
        default: return -1;
    }
}

int GrokBotAvatar::activeAgentState() const
{
    if (!transparent_eyes_ || wake_shy_until_us_ != 0) return -1;
    return search_until_us_ > esp_timer_get_time() ? grok_ball::kSearchState : agent_state_;
}

void GrokBotAvatar::startAgentState(int state, int64_t now)
{
    agent_state_ = state;
    agent_started_us_ = now;
}

void GrokBotAvatar::setSearching(bool active)
{
    const int64_t now = esp_timer_get_time();
    const bool was_active = search_until_us_ > now;
    // Refreshed by the bridge heartbeat. Clear automatically on disconnect.
    search_until_us_ = active ? now + 15000000 : 0;
    if (was_active == active) return;
    if (active) {
        search_started_us_ = now;
    } else if (agent_state_ < 0 && wake_shy_until_us_ == 0) {
        choose_clip_for_mode();
    }
    next_frame_us_ = now;
    ESP_LOGI(TAG, "Web search eyes %s", active ? "started" : "stopped");
}

bool GrokBotAvatar::showAgentState(int state, uint32_t duration_ms)
{
    if (!ready_ || !transparent_eyes_ || !grok_ball::valid_state(state)) return false;
    const int64_t now = esp_timer_get_time();
    startAgentState(state, now);
    agent_until_us_ = now + static_cast<int64_t>(duration_ms) * 1000;
    next_frame_us_ = now;
    return true;
}

void GrokBotAvatar::showWakeShy()
{
    if (!ready_) return;
    const int64_t now = esp_timer_get_time();
    if (wake_shy_until_us_ > now) return;
    removeDecorator(wake_shy_decorator_id_);
    // Reuse the firmware's existing pink blush, exactly as head-petting does.
    wake_shy_decorator_id_ = addDecorator(std::make_unique<ShyDecorator>(lv_obj_get_parent(panel_), 0));
    wake_shy_until_us_ = now + 1200000;
    select_clip("shy");
    next_frame_us_ = now;
    ESP_LOGI(TAG, "Wake shy started (1200 ms, existing pink blush)");
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
    if (agent_until_us_ == 0) {
        startAgentState(agentStateForMode(), esp_timer_get_time());
    }
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

void GrokBotAvatar::invalidate_image(const AlphaBounds* bounds)
{
    if (image_ == nullptr) return;
    lv_image_cache_drop(&image_dsc_);
    if (!transparent_eyes_) {
        lv_obj_invalidate(image_);
        return;
    }
    const auto current = bounds ? *bounds : packed_alpha_bounds(packed_, player_.width(), player_.height());
    if (full_redraw_pending_) {
        // In particular, erase the old opaque head when switching skins.
        lv_obj_invalidate(image_);
        full_redraw_pending_ = false;
    } else {
        // Include both footprints: blinking/moving eyes must erase old pixels.
        const auto dirty = union_alpha_bounds(previous_eyes_bounds_, current);
        if (dirty.valid()) {
            lv_area_t image_area;
            lv_obj_get_coords(image_, &image_area);
            lv_area_t area = {image_area.x1 + dirty.x1, image_area.y1 + dirty.y1,
                              image_area.x1 + dirty.x2, image_area.y1 + dirty.y2};
            lv_obj_invalidate_area(image_, &area);
        }
    }
    previous_eyes_bounds_ = current;
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
    // State changes are still recorded during feedback. Keep the shy clip for
    // this one-shot, then resolve the latest listening/speaking mode on expiry.
    if (wake_shy_until_us_ > esp_timer_get_time()) {
        return strcmp(currentClipName(), "shy") == 0 || select_clip("shy");
    }
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
    ESP_LOGI(TAG, "fps=%lu clip=%s frame=%d/%d decode=%luus agent=%d", static_cast<unsigned long>(frames), currentClipName(),
             player_.current_index(), player_.selected_frame_count(), static_cast<unsigned long>(avg_decode_us), activeAgentState());
    frames_since_log_    = 0;
    decode_us_since_log_ = 0;
    next_log_us_         = now_us + 1000000;
}

}  // namespace stackchan::avatar
