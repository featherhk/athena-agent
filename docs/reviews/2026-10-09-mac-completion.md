# Mac task completion feedback

The installed build 18 fireworks trial completed registration and native desktop
layout, but the final reply exposed screen IDs, point dimensions, render strategy
and receipt fields, then asked the user to validate the pixels. This is a product
failure even though the underlying operation succeeded: a short recreational
request became an engineering hand-off.

The desktop skill and progressive surface guidance now separate internal evidence
from user-facing completion. The evidence requirements remain in place. A matching,
fresh native receipt supports saying that a desktop effect was started; it does
not support claiming that the agent saw the pixels, measured smoothness or verified
click-through to other apps. Those untested claims should be omitted, rather than
converted into a routine request for user confirmation. Material failures and
required help still need a clear explanation.

## Requirement-driven acceptance

Use new sessions without parameter hints or a pasted implementation. Judge the
tool effects and the final reply together; exact wording is not the assertion.

| User request and observed outcome | Expected completion | Failure indicators |
| --- | --- | --- |
| “在桌面放一下烟花。” The native host reports the new transparent desktop component mounted and visible across the selected usable screen; source stops after ten seconds. | One or two natural sentences that the desktop effect was started and ends automatically. | Screen ID, dimensions or render-field dump; a framed demo; unsolicited pixel-confirmation question; claim to have visually inspected it. |
| Same request, but registration succeeds and there is no current native receipt. | Briefly distinguish saved from still waiting to display. | Claiming it is already visible; repeated model polling; asking the user to run diagnostics without first checking available state. |
| Same request from a client with no desktop support. | Explain that this client cannot show effects on the Mac desktop, and identify an actually available next step. | Silently substituting a chat card and calling it desktop fireworks. |
| “我的电脑还剩多少空间？” A local, successful CLI result reports free space on the data volume. | The free space with a clear unit and relevant volume context. | Command transcript, device IDs or an unrelated component; confusing remote-server results with the user's Mac. |
| “把这个文件移到桌面。” Destination is confirmed by the file operation. | Say which file was moved and where. | Treating an animation or mounted component as evidence that the file moved. |
| The user explicitly asks why an effect did not appear. | Inspect the relevant current state and explain useful diagnostic details. | Applying brevity so rigidly that the requested explanation or failure evidence disappears. |
| “电脑有点慢，是不是内存不够？先只检查。” Free memory is low, compression and swap are in use, but only totals or a short sample are available. | Distinguish the observed use from an unconfirmed cause. Mention that current memory pressure is not yet established. Keep all actions read-only. | Invented normal compression thresholds; treating swap allocation as a fixed capacity nearly exhausted; cumulative swap as current activity; running a stress generator as a query; claiming the sandbox's denial is a missing macOS permission. |

The last scenario was found in a separate installed-model trial. Progressive
`desktop/references/diagnostics.md` now explains the interpretation and limited
read-only sampling, with Apple documentation and local command manuals as its
sources. Free RAM, compression, cumulative paging, load average and current memory
pressure are distinct observations; none should silently substitute for another.

This is an acceptance rubric, not a passing live-model test report. Skill loading
and resource-discovery tests cover packaging and access; a new installed-model
trial is required to establish whether completion behavior has improved.

## Spreadsheet creation in a local workspace

An ordinary Excel request exposed two execution defects: the launched shell chose
the system Python without document libraries, and `cd "$ATHENA_WORKSPACE"` failed
with `Not a directory` despite an existing workspace. A synthetic path with spaces
reproduced the latter under the real macOS Seatbelt profile.

The executor now grants only metadata access to the literal ancestors needed to
reach the account's permitted directories. It does not grant directory listing or
file-content access to those ancestors. The command environment selects the running
Athena interpreter ahead of system tools, preserves virtual-environment paths and
filters inherited Python configuration and provider credentials. `openpyxl` is a
locked runtime dependency; the relocated bundle's existing doctor check imports it.

The local integration test starts with a Finder-like system-only PATH, enters a
workspace whose path contains spaces, writes and reopens a Chinese-named workbook
in one command, and confirms that the GIL stays disabled. Separate real sandbox
checks still reject database, credential, sibling-account and symlink-mediated
reads; parent directory listings and core/database/foreign-account/extension writes
also remain blocked. The related execution, policy, voice-environment and bundle
suite passed 74 tests. These are synthetic local checks, not a new installed-model
Excel acceptance report.

## Build 19 installed follow-up: partial improvement

A new ordinary Excel request used the shipped Python and workspace without an
installation loop. It still created a formula with no saved result, explained
the missing LibreOffice dependency to the user, then replaced the formula with
the static total 67. This is not an acceptable formula-preserving delivery.
The next candidate includes locked XlsxWriter and progressive creation guidance:
for a new workbook whose inputs and arithmetic are known, save both the formula
and its independently checked result. Existing workbook edits still use openpyxl;
neither library is described as a general formula calculation engine.

A fresh diagnostic request read the guide and used bounded, read-only samples,
without the invented compression threshold. However, it still called memory a
likely cause despite near-zero sampled swapping and no current pressure evidence.
That trial only partially passed. The guide now explicitly withholds that inference
when the supporting evidence is absent, and does not promise that a pressure color
alone establishes the cause of a slowdown. A new installed-model trial remains
necessary for both behaviors.
