# Mac application control

Desktop components and system control are separate capabilities. The component host manages Athena's own panels. `runtime op=computer` now connects the local backend to the native application's Accessibility client. It is an initial structured-control path, not complete computer use.

The native parent owns two anonymous pipes to its bundled child. No WebKit message, generated iframe, HTTP request flag, path, shell command or model-selected executable creates this connection. The standard runtime tool checks an owned session; hosted/cloud executors have no native connection. Agent subprocesses do not receive the control channel.

Supported operations are status, running-application inventory, bounded accessibility observation of one identified application, invocation of an observed AX action, and setting a writable nonsecure text field. The model must read the desktop skill before using them. There are no arbitrary AppleScripts, screenshots, coordinate clicks, global keystrokes or dragging in this first version. A custom-drawn application may not expose enough accessibility information.

`status` checks Athena's actual `AXIsProcessTrusted()` result without prompting. The fixed Mac menu links to Accessibility settings. Only the user grants OS access; the host never writes TCC records. Apple Events automation and Accessibility are distinct. In particular, an AppleScript `-10004` alone does not identify a missing user authorization or justify guessing helper-process names.

One physical desktop is serialized across sessions. Observations belong to the requesting conversation, expire after 30 seconds, and are consumed before an action. The native host rechecks process launch identity and element role, subrole, title and description. A target change requires a fresh observation. Secure field values and descendants are excluded. Observations are bounded by elapsed time, element count and depth, and report truncation.

Every admitted operation is recorded before dispatch in `computer_actions`. Transport sends once. A missing response or cancelled in-flight action is unknown; recovery never replays it. Pending native operations have deadlines and cancellation IDs; an already dispatched external action cannot be rolled back. A successful AX invocation is a dispatch receipt, not proof of task completion: take another snapshot to verify the requested effect. Apple documents that `AXUIElementPerformAction` may time out even after the target has begun modal processing, so automatic retries are unsafe.

The API currently lives in the native host and a concrete backend adapter. This does not make execution, the agent loop or storage fully replaceable providers. Native fixtures and fake transport tests cannot substitute for real cross-application acceptance. Permission denial, closing only a selected preview, Finder menu actions, focus changes, cancellation, app termination and restart must each be verified before advertising broad Mac control.

References: [AX trust](https://developer.apple.com/documentation/applicationservices/1459186-axisprocesstrusted), [AX actions](https://developer.apple.com/documentation/applicationservices/1462091-axuielementperformaction), [Apple Events privilege error](https://developer.apple.com/documentation/coreservices/1560036-anonymous/erraeprivilegeerror).
