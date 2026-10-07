# Sourced by build.sh: librime + librime-lua + Beam merged into one 64-bit rime.dll.
# Keep Unicode user directories out of yaml-cpp's narrow filename overload.
if grep -Fq 'YAML::LoadFile(file_path.string())' "$S/librime/src/rime/config/config_data.cc"; then
  patch --batch -d "$S/librime" -p1 <<'PATCH_YAML'
--- a/src/rime/config/config_data.cc
+++ b/src/rime/config/config_data.cc
@@ -72,7 +72,9 @@
   }
   LOG(INFO) << "loading config file '" << file_path << "'.";
   try {
-    YAML::Node doc = YAML::LoadFile(file_path.string());
+    std::ifstream input(file_path.c_str());
+    if (!input) throw YAML::BadFile(file_path.u8string());
+    YAML::Node doc = YAML::Load(input);
     root = ConvertFromYaml(doc, compiler);
   } catch (YAML::Exception& e) {
     LOG(ERROR) << "Error parsing YAML \"" << file_path << "\" : " << e.what();
PATCH_YAML
fi
# Lua uses UTF-8 paths independently of the host's ANSI code page.
if ! grep -Fq 'beam_fopen_utf8' "$S/librime-lua/thirdparty/lua5.4/lauxlib.h"; then
  patch --batch -d "$S/librime-lua/thirdparty" -p1 <<'PATCH_LUA_IO'
diff --git a/lua5.4/lauxlib.c b/lua5.4/lauxlib.c
index f9a4538..1019340 100644
--- a/lua5.4/lauxlib.c
+++ b/lua5.4/lauxlib.c
@@ -28,0 +29,27 @@
+#if defined(_WIN32)
+#include <windows.h>
+#endif
+
+/* librime paths are UTF-8 regardless of the host's ANSI code page. */
+LUALIB_API FILE *beam_fopen_utf8 (const char *filename, const char *mode,
+                                FILE *stream) {
+#if defined(_WIN32)
+  int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, filename, -1, NULL, 0);
+  wchar_t wide_mode[16];
+  wchar_t *path;
+  FILE *file;
+  if (!size || !MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, mode, -1, wide_mode, 16)) {
+    errno = EINVAL;
+    return NULL;
+  }
+  path = (wchar_t *)malloc(size * sizeof(wchar_t));
+  if (!path) { errno = ENOMEM; return NULL; }
+  MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, filename, -1, path, size);
+  file = stream ? _wfreopen(path, wide_mode, stream) : _wfopen(path, wide_mode);
+  free(path);
+  return file;
+#else
+  return stream ? freopen(filename, mode, stream) : fopen(filename, mode);
+#endif
+}
+
@@ -810 +837 @@ LUALIB_API int luaL_loadfilex (lua_State *L, const char *filename,
-    lf.f = fopen(filename, "r");
+    lf.f = beam_fopen_utf8(filename, "r", NULL);
@@ -820 +847 @@ LUALIB_API int luaL_loadfilex (lua_State *L, const char *filename,
-      lf.f = freopen(filename, "rb", lf.f);  /* reopen in binary mode */
+      lf.f = beam_fopen_utf8(filename, "rb", lf.f);  /* reopen in binary mode */
@@ -1139 +1165,0 @@ LUALIB_API void luaL_checkversion_ (lua_State *L, lua_Number ver, size_t sz) {
-
diff --git a/lua5.4/lauxlib.h b/lua5.4/lauxlib.h
index 5b977e2..18fd91c 100644
--- a/lua5.4/lauxlib.h
+++ b/lua5.4/lauxlib.h
@@ -83,0 +84,3 @@ LUALIB_API int (luaL_execresult) (lua_State *L, int stat);
+LUALIB_API FILE *beam_fopen_utf8 (const char *filename, const char *mode,
+                                FILE *stream);
+
@@ -301 +303,0 @@ typedef struct luaL_Stream {
-
diff --git a/lua5.4/liolib.c b/lua5.4/liolib.c
index c5075f3..395c5b6 100644
--- a/lua5.4/liolib.c
+++ b/lua5.4/liolib.c
@@ -263 +263 @@ static void opencheck (lua_State *L, const char *fname, const char *mode) {
-  p->f = fopen(fname, mode);
+  p->f = beam_fopen_utf8(fname, mode, NULL);
@@ -276 +276 @@ static int io_open (lua_State *L) {
-  p->f = fopen(filename, mode);
+  p->f = beam_fopen_utf8(filename, mode, NULL);
@@ -841 +840,0 @@ LUAMOD_API int luaopen_io (lua_State *L) {
-
diff --git a/lua5.4/loadlib.c b/lua5.4/loadlib.c
index 6d289fc..2bea239 100644
--- a/lua5.4/loadlib.c
+++ b/lua5.4/loadlib.c
@@ -426 +426 @@ static int readable (const char *filename) {
-  FILE *f = fopen(filename, "r");  /* try to open file */
+  FILE *f = beam_fopen_utf8(filename, "r", NULL);  /* try to open file */
@@ -758 +757,0 @@ LUAMOD_API int luaopen_package (lua_State *L) {
-
PATCH_LUA_IO
fi
if ! grep -Fq 'beam_fopen_utf8' "$S/librime-lua/src/modules.cc"; then
  patch --batch -d "$S/librime-lua" -p1 <<'PATCH_LUA_PATH'
diff --git a/src/modules.cc b/src/modules.cc
index 6ca704e..6a0788b 100644
--- a/src/modules.cc
+++ b/src/modules.cc
@@ -12 +12 @@ static bool file_exists(const char *fname) noexcept {
-    FILE * const fp = fopen(fname, "r");
+    FILE * const fp = beam_fopen_utf8(fname, "r", nullptr);
@@ -37 +37 @@ struct COMPAT<T, void_t<decltype(std::declval<T>().user_data_dir.string())>> {
-    // path::string() returns native encoding on Windows
+    // Lua paths use UTF-8, including on Windows.
@@ -39 +39 @@ struct COMPAT<T, void_t<decltype(std::declval<T>().user_data_dir.string())>> {
-    return deployer.shared_data_dir.string();
+    return deployer.shared_data_dir.u8string();
@@ -44 +44 @@ struct COMPAT<T, void_t<decltype(std::declval<T>().user_data_dir.string())>> {
-    return deployer.user_data_dir.string();
+    return deployer.user_data_dir.u8string();
diff --git a/src/types.cc b/src/types.cc
index b8d6484..10ceb76 100644
--- a/src/types.cc
+++ b/src/types.cc
@@ -100 +100 @@ struct COMPAT<T, void_t<decltype(std::declval<T>().user_data_dir.string())>> {
-    return deployer.shared_data_dir.string();
+    return deployer.shared_data_dir.u8string();
@@ -105 +105 @@ struct COMPAT<T, void_t<decltype(std::declval<T>().user_data_dir.string())>> {
-    return deployer.user_data_dir.string();
+    return deployer.user_data_dir.u8string();
@@ -110 +110 @@ struct COMPAT<T, void_t<decltype(std::declval<T>().user_data_dir.string())>> {
-    return deployer.sync_dir.string();
+    return deployer.sync_dir.u8string();
PATCH_LUA_PATH
fi
ln -sfn /usr/include/nlohmann "$P/include/nlohmann"
mkdir -p "$S/librime/plugins"
ln -sfn "$S/librime-lua" "$S/librime/plugins/lua"
if [ -d "$S/librime/plugins/beam" ] && [ ! -L "$S/librime/plugins/beam" ]; then
  mv "$S/librime/plugins/beam" "$S/librime/plugins/beam.previous.$(date +%s)"
fi
ln -sfn "$root/src/rime" "$S/librime/plugins/beam"
# The plugin list is fixed so a stray directory under plugins/ cannot sneak in.
RIME_PLUGINS="lua beam" cmake -S "$S/librime" -B "$W/b/librime" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$T" \
    -DBEAM_WIN_PREFIX="$P" -DBEAM_SOURCE_ROOT="$root" -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX="$W/b/librime-install" \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_FLAGS="-include cstdint" \
  -DBUILD_STATIC=ON -DBUILD_SHARED_LIBS=ON -DBUILD_MERGED_PLUGINS=ON -DBUILD_TEST=OFF \
  -DENABLE_LOGGING=ON -DENABLE_EXTERNAL_PLUGINS=OFF -DBoost_INCLUDE_DIR="$P/include" \
  > "$W/b-librime.log" 2>&1
cmake --build "$W/b/librime" >> "$W/b-librime.log" 2>&1
mkdir -p "$W/out"
cp "$W/b/librime/lib/rime.dll" "$W/out/rime.dll" 2>/dev/null || cp "$W"/b/librime/bin/*rime*.dll "$W/out/rime.dll"
mkdir -p "$W/symbols"
${TRIPLE}-objcopy --only-keep-debug "$W/out/rime.dll" "$W/symbols/rime.dll.debug"
${TRIPLE}-strip "$W/out/rime.dll"
${TRIPLE}-g++ -std=c++17 -municode -mwindows -static -I"$S/librime/src" \
  "$root/tests/windows-rime-host.cc" -o "$W/out/beam-rime-host-test.exe"
echo "built out/rime.dll"
