/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "hal_bridge.h"
#include "stackchan_display.h"
#include <esp_log.h>
#include <esp_err.h>
#include <esp_heap_caps.h>
#include <mbedtls/base64.h>
#include <cstdlib>
#include <atomic>
#include <algorithm>
#include <cstdio>
#include <nvs.h>
#include <nvs_flash.h>
#include <driver/gpio.h>
#include <driver/usb_serial_jtag.h>
#include <freertos/idf_additions.h>
#include <esp_event.h>
#include <application.h>
#include <cJSON.h>
#include <board.h>
#include <display.h>
#include <mutex>
#include <assets.h>
#include <settings.h>

// Called locally as soon as the wake-word event is accepted, before network I/O.
void stackchan_wake_feedback()
{
    if (auto* display = dynamic_cast<StackChanAvatarDisplay*>(Board::GetInstance().GetDisplay())) {
        display->ShowWakeShy();
    }
}

static const char* _tag = "HAL_BRIDGE";

static constexpr std::string_view _xiaozhi_config_nvs_ns                           = "xiaozhi";
static constexpr std::string_view _xiaozhi_config_idle_shutdown_time_key           = "idle_sec";
static constexpr std::string_view _xiaozhi_config_allow_shutdown_when_charging_key = "ext_pwr";
static constexpr std::string_view _xiaozhi_config_idle_random_movement_key         = "idle_lv";
static constexpr std::string_view _xiaozhi_config_idle_motion_disabled_key         = "idle_off_v1";
static constexpr std::string_view _xiaozhi_config_start_ai_agent_on_boot_key       = "boot_ai";


namespace {
std::mutex capture_mutex;
int16_t* capture_buffer = nullptr;
size_t capture_size = 0;
size_t capture_capacity = 0;
}

static std::atomic_bool chat_output_pending{false};

void stackchan_arm_chat_output()
{
    chat_output_pending = true;
}

void stackchan_chat_output(const int16_t* samples, size_t count, int volume)
{
    if (chat_output_pending.load() &&
        std::any_of(samples, samples + count, [](int16_t sample) { return sample > 32 || sample < -32; }) &&
        chat_output_pending.exchange(false)) {
        ESP_LOGI("USBChat", "Speech audio delivered to speaker, volume=%d", volume);
    }
}

void stackchan_capture_audio(const int16_t* samples, size_t count, int channels)
{
    std::lock_guard<std::mutex> lock(capture_mutex);
    for (size_t i = 0; capture_buffer && i < count && capture_size < capture_capacity; i += channels) {
        capture_buffer[capture_size++] = samples[i];
    }
}

static bool usb_write(std::string_view line)
{
    return usb_serial_jtag_write_bytes(line.data(), line.size(), pdMS_TO_TICKS(1000)) ==
           static_cast<int>(line.size());
}

static void usb_record(int seconds)
{
    auto& app = Application::GetInstance();
    if (app.GetDeviceState() != kDeviceStateIdle) {
        usb_write("STACKCHAN_RECORD_ERROR busy\n");
        return;
    }
    const size_t capacity = seconds * 16000;
    auto* audio = static_cast<int16_t*>(heap_caps_malloc(capacity * sizeof(int16_t), MALLOC_CAP_SPIRAM));
    if (!audio) {
        usb_write("STACKCHAN_RECORD_ERROR memory\n");
        return;
    }

    {
        std::lock_guard<std::mutex> lock(capture_mutex);
        capture_size = 0;
        capture_capacity = capacity;
        capture_buffer = audio;
    }
    usb_write("STACKCHAN_RECORD_START\n");

    const auto deadline = xTaskGetTickCount() + pdMS_TO_TICKS((seconds + 2) * 1000);
    size_t samples = 0;
    bool interrupted = false;
    while (true) {
        interrupted = app.GetDeviceState() != kDeviceStateIdle;
        const bool expired = xTaskGetTickCount() >= deadline;
        {
            std::lock_guard<std::mutex> lock(capture_mutex);
            if (interrupted || expired || capture_size == capacity) {
                samples = capture_size;
                capture_buffer = nullptr;
                break;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }

    if (interrupted || samples != capacity) {
        usb_write("STACKCHAN_RECORD_ERROR capture interrupted\n");
    } else {
        const auto* bytes = reinterpret_cast<const unsigned char*>(audio);
        const size_t length = samples * sizeof(int16_t);
        for (size_t offset = 0; offset < length; offset += 120) {
            unsigned char encoded[161];
            size_t written = 0;
            mbedtls_base64_encode(encoded, sizeof(encoded), &written,
                                  bytes + offset, std::min<size_t>(120, length - offset));
            char line[180];
            const auto size = snprintf(line, sizeof(line), "STACKCHAN_PCM %.*s\n",
                                       static_cast<int>(written), encoded);
            if (!usb_write({line, static_cast<size_t>(size)})) break;
        }
        usb_write("STACKCHAN_RECORD_END\n");
    }
    free(audio);
}

namespace hal_bridge {

static void usb_speech_task(void*)
{
    constexpr std::string_view prefix = "STACKCHAN_SAY ";
    std::string line;
    bool overflow = false;
    uint8_t buffer[64];
    while (true) {
        const int count = usb_serial_jtag_read_bytes(buffer, sizeof(buffer), pdMS_TO_TICKS(250));
        for (int i = 0; i < count; ++i) {
            const char ch = static_cast<char>(buffer[i]);
            if (ch == '\n') {
                if (!overflow && line.size() == 18 && line.starts_with("STACKCHAN_RECORD ") &&
                    line.back() >= '1' && line.back() <= '9') {
                    usb_record(line.back() - '0');
#ifdef CONFIG_STACKCHAN_CODEX_VOICE_USB
                } else if (!overflow && line.starts_with("STACKCHAN_AGENT_SYNC ")) {
                    auto* payload = cJSON_Parse(line.c_str() + strlen("STACKCHAN_AGENT_SYNC "));
                    auto* jobs = cJSON_GetObjectItem(payload, "jobs");
                    if (cJSON_IsArray(jobs) && cJSON_GetArraySize(jobs) <= 32) {
                        std::vector<int> ids;
                        bool valid = true;
                        cJSON* job;
                        cJSON_ArrayForEach(job, jobs) {
                            if (!cJSON_IsNumber(job) || job->valuedouble != job->valueint || job->valueint <= 0) {
                                valid = false;
                                break;
                            }
                            ids.push_back(job->valueint);
                        }
                        if (valid) Application::GetInstance().Schedule([ids = std::move(ids)]() {
                            Application::GetInstance().SyncLocalTasks(ids);
                        });
                    }
                    cJSON_Delete(payload);
                } else if (!overflow && line.starts_with("STACKCHAN_FLOMO_RESULT ")) {
                    auto* result = cJSON_Parse(line.c_str() + strlen("STACKCHAN_FLOMO_RESULT "));
                    auto* id = cJSON_GetObjectItem(result, "id");
                    auto* ok = cJSON_GetObjectItem(result, "ok");
                    if (cJSON_IsNumber(id) && cJSON_IsBool(ok)) {
                        if (auto* json = cJSON_PrintUnformatted(result)) {
                            Application::GetInstance().Schedule(
                                [job_id = id->valueint, text = std::string(json), error = !cJSON_IsTrue(ok)]() {
                                    Application::GetInstance().CompleteFlomoOperation(job_id, text, error);
                                });
                            cJSON_free(json);
                        }
                    }
                    cJSON_Delete(result);
                } else if (!overflow && line.starts_with("STACKCHAN_WEBSEARCH_RESULT ")) {
                    auto* result = cJSON_Parse(line.c_str() + strlen("STACKCHAN_WEBSEARCH_RESULT "));
                    auto* id = cJSON_GetObjectItem(result, "id");
                    auto* ok = cJSON_GetObjectItem(result, "ok");
                    if (cJSON_IsNumber(id) && cJSON_IsBool(ok)) {
                        if (auto* json = cJSON_PrintUnformatted(result)) {
                            Application::GetInstance().Schedule(
                                [job_id = id->valueint, text = std::string(json), error = !cJSON_IsTrue(ok)]() {
                                    Application::GetInstance().CompleteWebSearch(job_id, text, error);
                                });
                            cJSON_free(json);
                        }
                    }
                    cJSON_Delete(result);
                } else if (!overflow && line.starts_with("STACKCHAN_THETA_RESULT ")) {
                    auto* result = cJSON_Parse(line.c_str() + strlen("STACKCHAN_THETA_RESULT "));
                    auto* id = cJSON_GetObjectItem(result, "id");
                    auto* ok = cJSON_GetObjectItem(result, "ok");
                    auto* text = cJSON_GetObjectItem(result, "text");
                    if (cJSON_IsNumber(id) && cJSON_IsBool(ok) && cJSON_IsString(text)) {
                        Application::GetInstance().Schedule(
                            [job_id = id->valueint, text = std::string(text->valuestring), error = !cJSON_IsTrue(ok)]() {
                                Application::GetInstance().CompleteThetaQuery(job_id, text, error);
                            });
                    }
                    cJSON_Delete(result);
                } else if (!overflow && line.starts_with("STACKCHAN_FUTU_RESULT ")) {
                    auto* result = cJSON_Parse(line.c_str() + strlen("STACKCHAN_FUTU_RESULT "));
                    auto* id = cJSON_GetObjectItem(result, "id");
                    auto* ok = cJSON_GetObjectItem(result, "ok");
                    if (cJSON_IsNumber(id) && cJSON_IsBool(ok)) {
                        if (auto* json = cJSON_PrintUnformatted(result)) {
                            Application::GetInstance().Schedule(
                                [job_id = id->valueint, text = std::string(json), error = !cJSON_IsTrue(ok)]() {
                                    Application::GetInstance().CompleteFutuQuery(job_id, text, error);
                                });
                            cJSON_free(json);
                        }
                    }
                    cJSON_Delete(result);
                } else if (!overflow && line.starts_with("STACKCHAN_CODEX_PROGRESS ")) {
                    auto* result = cJSON_Parse(line.c_str() + strlen("STACKCHAN_CODEX_PROGRESS "));
                    auto* id = cJSON_GetObjectItem(result, "id");
                    auto* sequence = cJSON_GetObjectItem(result, "seq");
                    auto* text = cJSON_GetObjectItem(result, "text");
                    if (cJSON_IsNumber(id) && cJSON_IsNumber(sequence) && cJSON_IsString(text) &&
                        strlen(text->valuestring) <= 600) {
                        Application::GetInstance().Schedule(
                            [job_id = id->valueint, seq = sequence->valueint,
                             update = std::string(text->valuestring)]() {
                                Application::GetInstance().UpdateCodexProgress(job_id, seq, update);
                            });
                    }
                    cJSON_Delete(result);
                } else if (!overflow && line.starts_with("STACKCHAN_CODEX_RESULT ")) {
                    auto* result = cJSON_Parse(line.c_str() + strlen("STACKCHAN_CODEX_RESULT "));
                    auto* id = cJSON_GetObjectItem(result, "id");
                    auto* text = cJSON_GetObjectItem(result, "text");
                    if (cJSON_IsNumber(id) && cJSON_IsString(text)) {
                        Application::GetInstance().Schedule(
                            [request_id = id->valueint, summary = std::string(text->valuestring)]() {
                                Application::GetInstance().CompleteCodexTask(request_id, summary);
                            });
                    }
                    cJSON_Delete(result);
#endif
                } else if (!overflow && line.starts_with("STACKCHAN_AVATAR ")) {
                    const auto selection = line.substr(strlen("STACKCHAN_AVATAR "));
                    if (selection == "white-eyes" || selection == "original") {
                        Application::GetInstance().Schedule([selection]() {
                            auto* display = dynamic_cast<StackChanAvatarDisplay*>(Board::GetInstance().GetDisplay());
                            const bool ok = display && display->SetAvatarSkin(selection == "white-eyes");
                            ESP_LOGI(_tag, "Avatar command: %s %s", selection.c_str(), ok ? "OK" : "FAILED");
                        });
                    } else if (selection == "wake-shy") {
                        Application::GetInstance().Schedule([]() { stackchan_wake_feedback(); });
                    } else if (selection == "snapshot") {
                        Application::GetInstance().Schedule([]() {
                            const auto state = Application::GetInstance().GetDeviceState();
                            if (state == kDeviceStateSpeaking || state == kDeviceStateListening) {
                                usb_write("STACKCHAN_AVATAR_SNAPSHOT_ERROR busy\n"); return;
                            }
                            std::string jpeg;
                            if (!Board::GetInstance().GetDisplay()->SnapshotToJpeg(jpeg, 90)) {
                                usb_write("STACKCHAN_AVATAR_SNAPSHOT_ERROR capture\n"); return;
                            }
                            usb_write("STACKCHAN_AVATAR_SNAPSHOT_BEGIN\n");
                            for (size_t offset = 0; offset < jpeg.size(); offset += 96) {
                                unsigned char encoded[129]; size_t length = 0;
                                if (mbedtls_base64_encode(encoded, sizeof(encoded), &length,
                                    reinterpret_cast<const unsigned char*>(jpeg.data() + offset),
                                    std::min(size_t(96), jpeg.size() - offset)) != 0) break;
                                usb_write("STACKCHAN_AVATAR_SNAPSHOT_DATA " +
                                    std::string(reinterpret_cast<char*>(encoded), length) + "\n");
                            }
                            usb_write("STACKCHAN_AVATAR_SNAPSHOT_END\n");
                        });
                    } else if (selection == "status") {
                        // NVS reads may also disable flash cache. The USB reader
                        // has a PSRAM stack, so both reads and writes belong on
                        // the application's internal-RAM task.
                        Application::GetInstance().Schedule([]() {
                            ESP_LOGI(_tag, "Avatar preference: %s", StackChanAvatarDisplay::WhiteEyesSelected() ? "white-eyes" : "original");
                        });
                    } else {
                        ESP_LOGW(_tag, "Invalid avatar selection");
                    }
                } else if (!overflow && line.starts_with("STACKCHAN_CHAT ")) {
                    const auto* payload = line.c_str() + sizeof("STACKCHAN_CHAT ") - 1;
                    auto* decoded = cJSON_ParseWithOpts(payload, nullptr, true);
                    if (cJSON_IsString(decoded) && decoded->valuestring[0] != '\0') {
                        Application::GetInstance().Schedule([message = std::string(decoded->valuestring)]() {
                            Application::GetInstance().SendChatText(message);
                        });
                    } else {
                        ESP_LOGW(_tag, "USB chat invalid text");
                    }
                    cJSON_Delete(decoded);
                } else if (!overflow && line.starts_with(prefix)) {
                    auto url = line.substr(prefix.size());
                    if (url.starts_with("http://") && url.find_first_of(" \r\t") == std::string::npos) {
                        Application::GetInstance().Schedule([url = std::move(url)]() mutable {
                            auto& app = Application::GetInstance();
                            if (app.GetDeviceState() != kDeviceStateIdle) {
                                ESP_LOGW(_tag, "USB speech busy");
                                return;
                            }
                            app.StartNotification(std::move(url), {});
                            if (app.GetDeviceState() != kDeviceStateNotifying) {
                                ESP_LOGW(_tag, "USB speech failed to start");
                            }
                        });
                    }
                }
                line.clear();
                overflow = false;
            } else if (line.size() < 4096) {
                line += ch;
            } else {
                overflow = true;
            }
        }
    }
}

/* -------------------------------------------------------------------------- */
/*                            State and touch point                           */
/* -------------------------------------------------------------------------- */

static std::mutex _mutex;
static Data_t _data;

void lock()
{
    _mutex.lock();
}

void unlock()
{
    _mutex.unlock();
}

Data_t& get_data()
{
    return _data;
}

void set_touch_point(int num, int x, int y)
{
    std::lock_guard<std::mutex> lock(_mutex);
    _data.touchPoint.num = num;
    _data.touchPoint.x   = x;
    _data.touchPoint.y   = y;
}

TouchPoint_t get_touch_point()
{
    std::lock_guard<std::mutex> lock(_mutex);
    return _data.touchPoint;
}

bool is_xiaozhi_mode()
{
    std::lock_guard<std::mutex> lock(_mutex);
    return _data.isXiaozhiMode;
}

void set_xiaozhi_mode(bool mode)
{
    std::lock_guard<std::mutex> lock(_mutex);
    _data.isXiaozhiMode = mode;
}

/* -------------------------------------------------------------------------- */
/*                                   Display                                  */
/* -------------------------------------------------------------------------- */
#define DISPLAY_TYPE StackChanAvatarDisplay

lv_disp_t* display_get_lvgl_display()
{
    auto display = static_cast<DISPLAY_TYPE*>(Board::GetInstance().GetDisplay());
    return display->GetLvglDisplay();
}

void disply_lvgl_lock()
{
    auto display = static_cast<DISPLAY_TYPE*>(Board::GetInstance().GetDisplay());
    display->LvglLock();
}

void disply_lvgl_unlock()
{
    auto display = static_cast<DISPLAY_TYPE*>(Board::GetInstance().GetDisplay());
    display->LvglUnlock();
}

/* -------------------------------------------------------------------------- */
/*                                 Application                                */
/* -------------------------------------------------------------------------- */

void xiaozhi_board_init()
{
    // Init board
    auto& board = Board::GetInstance();
}

void start_xiaozhi_app()
{
    set_xiaozhi_mode(true);

    // Initialize and run the application
    auto& app = Application::GetInstance();
    app.Initialize();
    usb_serial_jtag_driver_config_t usb_config = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    if (usb_serial_jtag_driver_install(&usb_config) != ESP_OK ||
        // Keep internal RAM available for the UDP receive task. This reader
        // only parses USB messages and schedules app work; it never writes flash.
        xTaskCreateWithCaps(usb_speech_task, "usb_speech", 4096, nullptr, 6, nullptr,
                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        ESP_LOGW(_tag, "USB speech unavailable");
    }
    app.Run();  // This function runs the main event loop and never returns
}

XiaozhiConfig_t get_xiaozhi_config()
{
    XiaozhiConfig_t config;

    Settings settings(_xiaozhi_config_nvs_ns.data(), false);
    config.idleShutdownTimeSeconds = settings.GetInt(_xiaozhi_config_idle_shutdown_time_key.data(),
                                                     static_cast<int>(config.idleShutdownTimeSeconds));
    config.allowShutdownWhenCharging =
        settings.GetBool(_xiaozhi_config_allow_shutdown_when_charging_key.data(), config.allowShutdownWhenCharging);
    config.idleRandomMovementLevel =
        settings.GetInt(_xiaozhi_config_idle_random_movement_key.data(), config.idleRandomMovementLevel);
    config.startAiAgentOnBoot =
        settings.GetBool(_xiaozhi_config_start_ai_agent_on_boot_key.data(), config.startAiAgentOnBoot);

    // Disable the old default idle head movement once, including on devices that
    // already have the previous default stored in NVS. The settings UI can still
    // turn it back on later without this migration overriding that choice.
    if (!settings.GetBool(_xiaozhi_config_idle_motion_disabled_key.data(), false)) {
        Settings writable_settings(_xiaozhi_config_nvs_ns.data(), true);
        writable_settings.SetInt(_xiaozhi_config_idle_random_movement_key.data(), 0);
        writable_settings.SetBool(_xiaozhi_config_idle_motion_disabled_key.data(), true);
        config.idleRandomMovementLevel = 0;
    }

    return config;
}

void set_xiaozhi_config(const XiaozhiConfig_t& config)
{
    Settings settings(_xiaozhi_config_nvs_ns.data(), true);
    settings.SetInt(_xiaozhi_config_idle_shutdown_time_key.data(), config.idleShutdownTimeSeconds);
    settings.SetBool(_xiaozhi_config_allow_shutdown_when_charging_key.data(), config.allowShutdownWhenCharging);
    settings.SetInt(_xiaozhi_config_idle_random_movement_key.data(), config.idleRandomMovementLevel);
    settings.SetBool(_xiaozhi_config_start_ai_agent_on_boot_key.data(), config.startAiAgentOnBoot);
}

void app_play_sound(const std::string_view& sound)
{
    auto& app = Application::GetInstance();
    app.PlaySound(sound);
}

}  // namespace hal_bridge
