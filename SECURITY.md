# Security boundaries

FailShrink executes the explicitly supplied oracle command with the current
user's permissions. Never accept command arguments from untrusted trace content,
uploaded files or a remote API. It is not a sandbox. `shell=False` prevents shell
interpretation; it does not make a malicious executable safe.

Temporary candidate files are deleted after each invocation. Captured stdout and
stderr are not embedded in reports. They are held in temporary files, and each
stream is rejected above 65,536 bytes when the process finishes. A noisy process
can still consume disk before its timeout; there is no disk, memory or CPU quota.

On POSIX a dedicated process group is killed at timeout and after the main
command exits. Descendants that escape that group are not covered. On Windows
only the direct child is terminated. Commands can still access your files,
network, environment and external databases. Reset oracle state between calls.

Reduced traces retain application payloads. Reports retain event IDs and failure
signatures. Treat them as private when the inputs are private. Only an authored,
synthetic webhook example belongs in this repository; do not commit real traces,
credentials, customer records or output from internal systems.

Report issues without sensitive trace payloads or secrets. Include Python/OS
versions, the smallest sanitized example, and expected versus observed behavior.
