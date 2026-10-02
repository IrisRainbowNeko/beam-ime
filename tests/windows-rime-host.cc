// SPDX-License-Identifier: Apache-2.0
// Exercise logging initialization from a Unicode GUI host, as Weasel does.
#include <windows.h>
#include <rime_api.h>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    HMODULE module = LoadLibraryW(L"rime.dll");
    if (!module) return 10;
    auto get_api = reinterpret_cast<RimeApi* (*)()>(GetProcAddress(module, "rime_get_api"));
    if (!get_api) return 11;
    auto api = get_api();
    RIME_STRUCT(RimeTraits, traits);
    traits.app_name = "rime.beam-unicode-test";
    traits.log_dir = ".";
    traits.min_log_level = 2;
    api->setup(&traits);
    api->initialize(nullptr);
    api->finalize();
    return 0;
}
