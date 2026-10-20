# Portable restore staging

This page formerly described private staging of a file-based Work database.
That restore path has been retired; CAO does not stage, restore, or reactivate
file-based database artifacts.

Use the native PostgreSQL deployment backup and restore procedure in
[personal deployment](personal-deployment.md). Restore must target a separately
provisioned PostgreSQL deployment, verify its schemas and archive, and keep
restored Work blocked until preserved operations have been reconciled. Do not
point the deployment tools at an old local database file.

The supported memory archive formats are independent: see
[memory archives](memory-archives.md) for OKF directory and tar archives.
