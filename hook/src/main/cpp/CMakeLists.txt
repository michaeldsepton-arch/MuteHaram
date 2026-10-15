cmake_minimum_required(VERSION 3.22.1)
project(muteharam_native CXX)

# ShadowHook (inline hooking) comes from its Maven AAR via Android prefab.
find_package(shadowhook REQUIRED CONFIG)

add_library(muteharam_native SHARED aaudio_hook.cpp)
target_compile_options(muteharam_native PRIVATE -Wall -O2)
target_link_libraries(muteharam_native shadowhook::shadowhook log aaudio)
