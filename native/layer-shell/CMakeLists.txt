cmake_minimum_required(VERSION 3.20)

project(user_updater_layer_shell LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Widgets)
find_package(LayerShellQt REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)

if(NOT CMAKE_LIBRARY_OUTPUT_DIRECTORY)
    set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}")
endif()

Python3_add_library(_layer_shell MODULE WITH_SOABI layer_shell.cpp)
target_compile_features(_layer_shell PRIVATE cxx_std_17)
target_link_libraries(
    _layer_shell
    PRIVATE
        Python3::Module
        Qt6::Widgets
        LayerShellQt::Interface
)

set_target_properties(
    _layer_shell
    PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_LIBRARY_OUTPUT_DIRECTORY}"
)

file(MAKE_DIRECTORY "${CMAKE_LIBRARY_OUTPUT_DIRECTORY}")
configure_file(
    layer_shell_build.py.in
    "${CMAKE_LIBRARY_OUTPUT_DIRECTORY}/_layer_shell_build.py"
    @ONLY
)
