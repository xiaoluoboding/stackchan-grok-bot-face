# The following five lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.16)

# Keep compiler and linker arguments below Windows command-line limits.
if(CMAKE_GENERATOR MATCHES "^Ninja")
    set(CMAKE_NINJA_FORCE_RESPONSE_FILE TRUE CACHE INTERNAL "Use Ninja response files")
endif()

set(PROJECT_VER "1.6.2")
add_definitions(-DFIRMWARE_VERSION=\"${PROJECT_VER}\")

# Add this line to disable the specific warning
add_compile_options(-Wno-missing-field-initializers)

# Auto-load a local sdkconfig overlay (git-ignored) when present. Lets
# self-hosted / custom deployments pin CONFIG_STACKCHAN_SERVER_URL,
# CONFIG_OTA_URL, etc. without touching committed defaults.
# Manual equivalent: SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.local" idf.py build
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/sdkconfig.defaults.local")
    set(SDKCONFIG_DEFAULTS
        "${CMAKE_CURRENT_SOURCE_DIR}/sdkconfig.defaults;${CMAKE_CURRENT_SOURCE_DIR}/sdkconfig.defaults.local"
        CACHE STRING "ESP-IDF sdkconfig default files (auto-extended with local overlay)" FORCE)
    message(STATUS "StackChan: detected sdkconfig.defaults.local, applying overlay")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(stack-chan)

# esp-dsp 1.7.0 relies on a transitive <cmath> include that GCC 14 no longer
# provides. Keep the workaround scoped to that managed component.
if(TARGET __idf_espressif__esp-dsp)
    target_compile_options(__idf_espressif__esp-dsp PRIVATE
        "$<$<COMPILE_LANGUAGE:CXX>:SHELL:-include cmath>")
endif()
