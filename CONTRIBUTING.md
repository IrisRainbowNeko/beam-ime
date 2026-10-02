# Contributing

Start with the build guide and run `bash tools/build.sh tests`. For changes to
inference, add a real-model check; for installation changes, exercise rollback,
upgrade and preservation of user edits. Keep pull requests focused.

Report a bug with OS / package version, input-method version, reproduction steps
and the diagnostic command output. Use an invented input example where possible.
Do not attach a complete user dictionary or input log.

Changes to the wire protocol must preserve protocol v1 clients. Platform-specific
packaging belongs in `packaging/`. New third-party dependencies should retain
their upstream attribution and fixed source revision.
