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
