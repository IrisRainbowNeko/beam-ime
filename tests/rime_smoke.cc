// Drive librime headlessly with a key sequence and print the menu after each step.
//
//   rime_smoke USER_DATA_DIR SCHEMA_ID KEYS...
//
// Each KEYS argument is passed to simulate_key_sequence ("nhsj", "{space}", "{Return}", "1");
// "@name" / "@!name" set / clear an option instead.
// Uses the installed librime, so installed plugins (librime-beam.so) are loaded as in Fcitx5.
// RIME_SHARED_DATA_DIR overrides the shared data directory (/usr/share/rime-data).
#include <rime_api.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>

int main(int argc, char ** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: rime_smoke USER_DATA_DIR SCHEMA_ID KEYS...\n");
        return 2;
    }
    RimeApi * rime = rime_get_api();
    RIME_STRUCT(RimeTraits, traits);
    const char * shared = getenv("RIME_SHARED_DATA_DIR");
    traits.shared_data_dir = shared ? shared : "/usr/share/rime-data";
    traits.user_data_dir = argv[1];
    traits.app_name = "rime.beam-smoke";
    traits.min_log_level = 2;
    rime->setup(&traits);
    rime->initialize(nullptr);
    if (rime->start_maintenance(True)) rime->join_maintenance_thread();
    RimeSessionId session = rime->create_session();
    if (!rime->select_schema(session, argv[2])) {
        fprintf(stderr, "cannot select schema %s\n", argv[2]);
        return 1;
    }
    for (int i = 3; i < argc; ++i) {
        if (argv[i][0] == '@') {
            bool on = argv[i][1] != '!';
            rime->set_option(session, argv[i] + (on ? 1 : 2), on);
            continue;
        }
        rime->simulate_key_sequence(session, argv[i]);
        RIME_STRUCT(RimeCommit, commit);
        if (rime->get_commit(session, &commit)) {
            printf("[%s] commit: %s\n", argv[i], commit.text);
            rime->free_commit(&commit);
        }
        RIME_STRUCT(RimeContext, context);
        if (rime->get_context(session, &context)) {
            printf("[%s] preedit: %s\n", argv[i], context.composition.preedit ? context.composition.preedit : "");
            for (int k = 0; k < context.menu.num_candidates; ++k) {
                const RimeCandidate & c = context.menu.candidates[k];
                printf("   %d. %s%s%s\n", k + 1, c.text, c.comment ? "  " : "", c.comment ? c.comment : "");
            }
            rime->free_context(&context);
        }
    }
    rime->destroy_session(session);
    rime->finalize();
    return 0;
}
