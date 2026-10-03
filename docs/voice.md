# Voice calls

The call button starts continuous microphone capture. A local voice activity
detector ends each spoken turn after a short pause, transcribes it, and submits it
to the ordinary session actor. New assistant text is spoken in bounded chunks as
sentences become available. Speaking over Athena stops playback and interrupts the
active session actor. Clarification questions remain pending when their audio is
interrupted; spoken answers can complete a multi-question help request in order.

Text conversation, tools, recovery and account ownership remain the existing
runtime's responsibility. Voice does not maintain a second model history. A call
ends on navigation to another session, sign-out, reset, page close or audio
suspension. Hang-up stops microphone capture, outstanding audio requests and
playback; it does not undo a task already submitted to the assistant. The fixed
Stop control stops both the task and the call.

## Current implementation and limits

- Browser AudioWorklet captures mono PCM, with echo cancellation requested from
  the browser. Capture uses a small preroll, adaptive energy threshold and a
  750 ms end-of-turn pause. Utterances are bounded to about 29 seconds.
  The higher echo threshold applies only while a source is actually playing;
  waiting for synthesis/download/decode must not suppress a quiet interruption.
- The VAD is an initial energy detector, not a learned semantic endpointing model.
  Background speech, loud speaker echo and very quiet speech need real-device
  acceptance. Headphones reduce feedback during testing. This is a replaceable
  component, not a claim of production-grade full-duplex acoustic performance.
- This release uses a transcription → existing text agent → synthesis pipeline.
  It is not a native speech-to-speech Realtime/WebRTC model. Latency includes
  transcription, model generation and sentence synthesis.
- OpenAI-compatible transcription/speech endpoints and native Alibaba DashScope
  Qwen ASR/TTS protocols are implemented. No audio account is assumed to exist.
  Without valid operator configuration the UI explains that voice is unavailable
  before requesting microphone access.
- Qwen3-TTS-Flash does not list Arabic among its supported speech languages.
  The current DashScope adapter reports that limitation and declines an Arabic
  UI call instead of pretending complete language coverage. Another TTS adapter
  is needed for Arabic output.
- Call transcripts enter ordinary durable messages. Raw microphone audio and
  synthesized audio are held in memory and are not saved by Athena. Provider
  retention follows the selected provider's account policy.
- `voice_requests` stores owner, session, operation, model, status, input units,
  output size and allowed numeric provider usage. It stores no recording, speech
  text, signed download URL or provider credentials. Interrupted requests remain
  auditable and are never replayed after a restart.

## Operator configuration

Keys belong on the gateway. Do not distribute operator audio keys in a DMG,
frontend bundle, environment inherited by tools, or a user's onboarding flow.
`ATHENA_AUDIO_API_KEY_FILE` can name a private external file containing the audio
provider key; alternatively use `ATHENA_AUDIO_API_KEY`. The text model key is not
reused implicitly.

For Alibaba's documented Qwen protocol:

```sh
ATHENA_AUDIO_PROVIDER=dashscope
ATHENA_AUDIO_API_KEY_FILE=/private/operator/path/audio-key.txt
ATHENA_AUDIO_BASE_URL=https://dashscope.aliyuncs.com/api/v1
ATHENA_AUDIO_STT_BASE_URL=https://YOUR_WORKSPACE.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
ATHENA_AUDIO_STT_MODEL=qwen3-asr-flash
ATHENA_AUDIO_TTS_MODEL=qwen3-tts-flash
ATHENA_AUDIO_VOICE=Cherry
```

The ASR base URL should be copied from the operator's actual Model Studio
workspace. Region and key must match. `ATHENA_AUDIO_STT_BASE_URL` is optional only
for accounts that still support the shared DashScope endpoint. For Singapore,
use the corresponding workspace and region URLs rather than mixing region keys.

For compatible providers, set `ATHENA_AUDIO_PROVIDER=openai`, an HTTPS
`ATHENA_AUDIO_BASE_URL` ending in the API prefix, the key file, and explicit
`ATHENA_AUDIO_STT_MODEL`, `ATHENA_AUDIO_TTS_MODEL`, `ATHENA_AUDIO_VOICE` values.
Defaults are `gpt-4o-mini-transcribe`, `gpt-4o-mini-tts`, and `coral`; this is a
protocol default, not an assumption that the account can use those models.
As checked on 2026-10-04, OpenAI has announced retirement of these transcription
and speech models in February and January 2027 respectively. These compatibility
defaults are not a recommendation for new provisioning; see the
[official deprecation schedule](https://developers.openai.com/api/docs/deprecations).
Replacing them with a Realtime model requires a protocol adapter, not merely a
different model ID on the existing `/audio/speech` request.

The browser only calls authenticated same-origin Athena endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/voice` | Availability and language limitations |
| `POST /api/sessions/{id}/voice/transcribe` | Bounded mono 16-bit WAV to text |
| `POST /api/sessions/{id}/voice/speech` | Up to 600 characters to audio |

HTTPS is required remotely; localhost is a secure browser context. Native macOS
packaging must include microphone usage text and restrict WKWebView microphone
permission to the trusted main frame. Browser recognition services are not used.

## Cloud authentication and metering

`CloudVoiceService` forwards audio through the account gateway using the user's
cloud bearer token. The provider key remains on the gateway. Audio gateway
endpoints are `/v1/voice`, `/v1/voice/transcribe` and `/v1/voice/speech`.
The user's source session ID is namespaced by the immutable account ID for audit;
one user cannot attach a request to another user's audit session.

Hosted audio is closed by default. The DashScope duration/character billing
adapter additionally requires an explicit operator USD tariff:

```sh
ATHENA_AUDIO_STT_USD_PER_SECOND=<reviewed-rate>
ATHENA_AUDIO_TTS_USD_PER_10K_CHARACTERS=<reviewed-rate>
```

These are the account's published charge rates, not an inferred exchange rate or
claim that all regions have the same provider price. The implementation never
converts RMB to USD silently. Reserve a conservative maximum before the upstream
request: validated WAV seconds rounded up plus one second, or twice the number
of input text code points. Successful settlement uses the provider-reported
`usage.seconds` or `usage.characters`. Missing usage, cancellation, transport
failure and charges above the reservation retain an uncertain hold for operator
reconciliation. No uncertain request is refunded or retried automatically.

The metering interface is `reserve(user_id, request_id, operation, model, units)`
followed by `settle(reservation, status, usage)`. The current hosted meter supports
duration/character-priced DashScope models. OpenAI token-priced audio needs its
own reviewed pricing and usage adapter before hosted enablement. Local operator
testing of either protocol is supported without the cloud ledger.

## Verification

Unit and integration tests cover speech/noise endpointing, pause continuation,
WAV bounds, incremental response playback, barge-in, late transcription after
hang-up, permission races, provider cancellation, authenticated cloud proxying,
insufficient balance, reservation settlement and audio URL fetch restrictions.
They use synthetic PCM and mocked providers. They do not prove human microphone
quality, provider account access or end-to-end conversational latency. Those
remain a release gate until an audio provider is configured and a real call is
completed on Safari/WKWebView and the supported mobile browsers.

Official protocol references:

- [OpenAI transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- [OpenAI speech synthesis](https://developers.openai.com/api/docs/guides/text-to-speech)
- [Qwen ASR](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference)
- [Qwen TTS](https://help.aliyun.com/zh/model-studio/qwen-tts-api)
- [Alibaba model pricing](https://help.aliyun.com/zh/model-studio/model-pricing)

## Low-cost call direction (2026-10-04)

The first target is a hands-free call inside Athena. Dialling telephone numbers
would additionally require a telephony/SIP provider and separate carrier charges.
The direction below is a selection and acceptance plan, not an enabled service.

Use local speech detection, streaming cloud recognition, the existing Athena
session actor, and streaming synthesis. Keep the text model independent of the
speech providers. A second conversational agent must not duplicate tool execution,
memory or history just to give Athena a voice.

| Layer | Initial candidate | Why / boundary |
| --- | --- | --- |
| Acoustic speech detection | [Silero VAD](https://github.com/snakers4/silero-vad), client-side ONNX | MIT; no per-minute API charge. Load pinned same-origin assets on demand; benchmark Safari/WKWebView CPU, battery and start-up cost before replacing the energy detector. |
| End of turn | Bounded silence policy first; evaluate [Smart Turn v3.2](https://github.com/pipecat-ai/smart-turn) | VAD detects speech, not whether the speaker has finished. Smart Turn is a separate BSD-licensed audio turn detector with multilingual support; evaluate it against hesitations and sentence continuations. |
| Recognition | `qwen3-asr-flash-realtime` | A candidate for partial transcripts while the user speaks; the existing HTTP ASR adapter is not this WebSocket adapter. |
| Synthesis | `qwen3-tts-flash-realtime` | A candidate for text-in/audio-out streaming; Arabic still needs a separate verified voice route. |
| Reasoning and actions | Existing configured Athena model | Same session, task state, user-help and credit enforcement as text. |

Model processing should not pull PyTorch/ONNX native dependencies into the
free-threaded Python core. Prefer browser inference for small detectors; an
isolated service is an option if measured client constraints require it. Do not
purchase an always-on GPU merely to avoid inexpensive audio API calls at preview
traffic levels.

### Cost assumptions

[Alibaba's published prices](https://help.aliyun.com/zh/model-studio/model-pricing),
checked 2026-10-04, excluding temporary free quotas:

| Realtime model | Beijing | Singapore |
| --- | --- | --- |
| `qwen3-asr-flash-realtime` | CNY 0.00033 per input audio second | CNY 0.00066 per input audio second |
| `qwen3-tts-flash-realtime` | CNY 1 per 10,000 billable characters | CNY 0.954101 per 10,000 billable characters |

For an illustrative 60-minute Chinese conversation with 30 minutes of uploaded
audio and 6,000 generated Chinese characters (200 per spoken minute), the 12,000
billable TTS characters produce totals of about **CNY 1.79 / 2.33** respectively.
Chinese characters count twice under this tariff. If 60 minutes of audio are
uploaded, the totals become **CNY 2.39 / 3.52**. Punctuation, padding, rounding and
generated-but-interrupted speech can add cost. These estimates exclude the text
model, transport, taxes and hosting. Do not equate wall-clock call time, voiced
time and provider-billed input duration, or silently convert these prices into
Athena's USD credit tariff.

[Groq Whisper Large v3 Turbo](https://console.groq.com/docs/speech-to-text) is an
inexpensive alternative at USD 0.04 per audio hour, but each request is billed for
at least ten seconds. It is a file-transcription option, not a drop-in bidirectional
streaming ASR service; network latency and many short turns can outweigh the unit
price advantage. Keep it as a candidate for dictation rather than choosing it
solely from its hourly headline price.

### Required implementation and acceptance

The present code still has one shared audio provider/key, an RMS-only detector,
whole-utterance uploads, and buffered synthesis at both gateway and client.
`httpx.stream` alone does not make playback streaming: the browser currently
waits for a complete response before decoding it. The realtime model names above
must not be inserted into the existing HTTP configuration and advertised as done.

Implement and verify in this order:

1. Give recognition and synthesis independent operator profiles, capabilities and
   tariffs. Validate language/voice compatibility before acquiring the microphone.
2. Add PCM speech-detector and turn-detector interfaces. Preserve preroll, bounded
   capture and device cleanup. Pin local assets; fail visibly or use the documented
   fallback if loading fails. Measure false interruptions, not just speed.
3. Stream ASR partials as provisional text; only a committed turn can enqueue an
   action. Stream short TTS buffers with backpressure and a per-turn cancellation
   epoch. Never play stale audio after interruption, session navigation or hang-up.
4. Distinguish suspending playback from cancelling a task. Evaluate soft ducking
   for suspected speech and confirm a new user turn before interrupting execution;
   the explicit Stop control remains immediate. Track which response audio was
   actually played so future context need not assume the user heard its tail.
5. Preserve a recognized utterance when submission fails, scoped to its owner and
   session. Distinguish definitely-unsent input from an unknown server outcome;
   neither may cause an automatic duplicate action.
6. Record content-free latency and billed-unit metrics. Measure speech end to
   first audible response, local interruption delay, false cutoff rate and cost
   per call. Initial targets are p95 interruption within 250 ms and first response
   within 1.5 seconds for a short no-tool reply on a healthy network; these are
   targets, not current results. Long tasks need truthful progress and background
   continuity instead of fabricated quick answers.

Release gates include a real 20-turn provider-backed call on Safari/WKWebView and
mobile clients, low-volume interruption during synthesis and playback, speaker
echo, pauses mid-sentence, mixed-language speech, permission denial, muted input,
background/lock transitions, disconnect/reconnect, insufficient credit and
hang-up during each stage. Check the durable transcript and ledger alongside what
was heard. A browser tab that ends microphone capture in the background is not
equivalent to a native phone call that continues while the device is locked;
native audio-session/background support remains separate work.

The 2026-10-04 playback-threshold fix passed 601 Python tests (7 opt-in tests
skipped), 148 frontend tests, TypeScript and the production build. Python 3.14.8T
reports the GIL disabled after imports. New synthetic-audio regressions cover
quiet interruption while synthesis is pending, suppression of weak playback
echo, natural completion, cancelled late audio and hang-up. This fix is source
validation; it neither enables a cloud audio provider nor certifies a real call.
