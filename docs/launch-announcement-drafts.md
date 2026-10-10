# Developer-preview announcement drafts

Prepared 2026-10-10. **Not sent.** Use the source-launch versions below only after
the [source gates](open-source-launch.md#source-release-gates) pass and anonymous
access to the public repository works. Source and feedback links remain pending;
replace their placeholders only after anonymous access has been verified. The
demonstration and download links below are live. The demonstration is a sequence
of real Build 26 window screenshots, not a continuous video. Do not publish a
private repository link, an uncreated Discord invite, or a video placeholder.
If source remains private, use the fallback wording instead.

## 简体中文：源码公开后

Athena 的开发者预览开放了。

我们在探索一种更自然的 Mac 助手：一句话开始工作，需要时把可操作的结果
放到桌面上；补充文件后更新同一份结果，收起以后还能找回来。

这组 Build 26 分步截图展示了一个具体任务：调整照明报价后，原来的 Excel
文件和桌面面板一起更新，位置和尺寸保持不变。两份报价更新为 13,200 和
13,500，价差 300。数据全部为合成数据，截图来自真实应用窗口，并非连续视频。

源码采用 Apache-2.0。源码本地模式使用你自己的 Chat Completions 模型连接，
不需要 Feather 云账号，模型费用直接支付给供应商。目前提供下载的 Mac
预览包是另一种托管账号模式，需要已开通且有余额的账号；安装包尚未公证。

这仍是面向开发者的早期预览，完整桌面控制、全屏效果宿主、单入口自动任务
组织还需要继续实现和验证。我们希望找到 5–10 位愿意用真实任务试一周的人：
第一次在哪里卡住，补充材料能否接上，回来时是否找得到结果？

- 源码：[经匿名访问验证的仓库链接]
- 安装与限制：[Mac 预览下载](https://app.athena.featherhk.com/)
- 演示：[Build 26 真实分步截图](https://featherhk.com/athena/#demonstration)
- 反馈：[已启用的 Issues 链接]

## English: after source publication

Athena's developer preview is available.

We're building a Mac assistant that brings useful work surfaces to your desktop.
Start with a conversation, update the same result as new files arrive, put it
away, and return to it later.

The Build 26 walkthrough shows a concrete follow-up: change the lighting cost,
and Athena updates the original Excel file and desktop panel while preserving
its position and size. The quotes become 13,200 and 13,500, a difference of 300.
All data is synthetic. These are real application-window screenshots captured
as separate steps, not a continuous video.

The source is Apache-2.0 licensed. Local source mode uses your own Chat
Completions model connection without a Feather cloud account; you pay your
provider directly. The currently downloadable Mac preview is a separate hosted
account distribution requiring an enabled account with credit. It is not yet
notarized.

This is an early developer preview. Complete desktop control, a fullscreen
effects host, and automatic task organization behind one conversation are still
work ahead. We're looking for 5–10 people to try concrete tasks for a week and
tell us where starting, updating, or returning to their work breaks down.

- Source: [verified public repository]
- Installation and limitations: [Mac preview download](https://app.athena.featherhk.com/)
- Demo: [Build 26 screenshot walkthrough](https://featherhk.com/athena/#demonstration)
- Feedback: [enabled Issues page]

## Fallback while source remains private

中文：Athena 的 Mac 托管预览已可测试，需要已开通且有余额的账号。我们正在
完成源码公开前的最终核验，暂不将其称为已开源版本。当前预览安装包尚未公证。
[下载与安装限制](https://app.athena.featherhk.com/)；
[Build 26 真实分步截图](https://featherhk.com/athena/#demonstration)，展示补充报价后
更新原文件和原面板，保留位置与尺寸。演示仅使用合成数据，并非连续视频。

English: Athena's hosted Mac preview is available for testing with an enabled
account and credit. Source publication is still pending final verification; this
is not an open-source release announcement. The preview installer is not yet
notarized. [Download and limitations](https://app.athena.featherhk.com/).
The [Build 26 walkthrough](https://featherhk.com/athena/#demonstration) shows a
follow-up updating the original file and panel, with position and size retained.
It uses synthetic data and separately captured window screenshots, not a
continuous video.

Before sending, refresh the exact build, platform requirements, account policy
and links. Do not imply an organization, Discord, payment method, all-provider
compatibility or recorded demo exists unless separately verified. Keep customer
examples out of public support reports; request synthetic reproductions.
