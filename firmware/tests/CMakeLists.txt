cmake_minimum_required(VERSION 3.16)

project(stackchan_firmware_tests LANGUAGES CXX)

enable_testing()

add_executable(motion_math_test
    motion_math_test.cpp
    ../main/stackchan/motion/motion_math.cpp
)

target_include_directories(motion_math_test PRIVATE
    ../main
)

target_compile_features(motion_math_test PRIVATE cxx_std_17)

add_test(NAME motion_math_test COMMAND $<TARGET_FILE:motion_math_test>)

add_executable(avatar_dirty_bounds_test avatar_dirty_bounds_test.cpp)
target_include_directories(avatar_dirty_bounds_test PRIVATE ../main)
target_compile_features(avatar_dirty_bounds_test PRIVATE cxx_std_17)
add_test(NAME avatar_dirty_bounds_test COMMAND $<TARGET_FILE:avatar_dirty_bounds_test>)
