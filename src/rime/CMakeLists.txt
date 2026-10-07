# In-tree librime plugin, used by the Windows merged build.
set(plugin_name rime-beam)
add_library(rime-beam-objs OBJECT "${BEAM_SOURCE_ROOT}/src/rime/beam_plugin.cc"
  "${BEAM_SOURCE_ROOT}/src/rime/beam_client.cc" "${BEAM_SOURCE_ROOT}/src/engine/net.cpp")
target_include_directories(rime-beam-objs PRIVATE "${BEAM_SOURCE_ROOT}/src/engine"
  "${BEAM_WIN_PREFIX}/include")
target_compile_features(rime-beam-objs PRIVATE cxx_std_17)
file(STRINGS "${BEAM_SOURCE_ROOT}/VERSION" BEAM_VERSION LIMIT_COUNT 1)
target_compile_definitions(rime-beam-objs PRIVATE GLOG_USE_GLOG_EXPORT BEAM_VERSION="${BEAM_VERSION}")
set(plugin_objs $<TARGET_OBJECTS:rime-beam-objs> PARENT_SCOPE)
set(plugin_name ${plugin_name} PARENT_SCOPE)
set(plugin_modules beam PARENT_SCOPE)
set(plugin_deps ${rime_library} ${rime_gears_library} ws2_32 bcrypt PARENT_SCOPE)
