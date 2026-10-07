# Dental AI Receptionist

A voice AI administrator for dental clinics: it answers the phone, talks to the patient
in Russian, finds a free slot and books a first visit. Built July–September 2026 as a
startup with a co-founder; I wrote the code and set up the telephony.

Status: working prototype. Inbound calls through Voximplant work end to end. We didn't
get to a paying clinic.

## Three stages, one folder each

| Folder | Stage | What it is |
|---|---|---|
| `registrator/` | July | Text core: LLM agent with function calling over a slots database. CLI and FastAPI. |
| `dental-ai-mvp/` | August | Outbound version for an investor demo: a lead leaves a form, the agent calls back and books. LiveKit orchestration, Deepgram STT, TTS on ElevenLabs and then Yandex SpeechKit, SIP trunk, live dashboard. Paused. |
| `dental-ai-voximplant/` | September | Current version: real phone calls through Voximplant (Russian numbers, Yandex ASR/TTS, barge-in), our FastAPI backend runs the dialog and the bookings. |

Why outbound turned into inbound: the first idea was to call web leads back within five
minutes. Our [mystery-shopper](https://github.com/radmirbambo/mystery-shopper) agent
left leads at 56 clinics and showed that patients mostly phone instead of filling in
forms, so the product moved to answering inbound calls.

## How the current version works

```
patient's phone ◄──► Voximplant cloud
                     voxengine_inbound.js / voxengine_scenario.js:
                     call control, Yandex ASR + TTS, barge-in
                          │  HTTP on every dialog turn
                          ▼
                     server.py (FastAPI)
                     /api/vox/inbound   find the lead by caller number or create one
                     /api/vox/turn      patient text ► LLM ► reply text, hang up after booking
                     /api/vox/event     call status: calling, no_answer, failed
                          │
                          ▼
                     brain.py: system prompt + function calling
                       get_available_slots(date)
                       book_appointment(...)
                          │
                          ▼
                     db.py (SQLite) ──► /dashboard: leads, bookings, week slots
```

The agent doesn't chat freely. It can only call two functions, it never diagnoses and
never sells treatment over the phone. The dashboard has a text mode that runs the same
dialog without telephony, so the demo works even when the phone line doesn't.

## What was hard

- **Telephony in Russia.** ElevenLabs blocks Russian IPs, so TTS moved to Yandex
  SpeechKit. Renting a Russian number for outbound calls needs a verified account and a
  qualified e-signature, which is why the current version starts with inbound calls.
- **LiveKit or Voximplant.** LiveKit Cloud runs in Germany: extra latency from Russia and
  personal data leaving the country, and a Russian number would still need our own SIP
  operator contract. Voximplant gives Russian numbers and Yandex speech out of the box at about 4–5 ₽ per minute in total. The comparison
  is in [docs/voice-infra-comparison.md](docs/voice-infra-comparison.md).
- **Turn-taking on a phone line.** The VoxEngine scenario handles barge-in, so the
  patient can interrupt the agent mid-sentence.

## Not done yet

- Outbound calls from a rented number (waiting on account verification).
- Backend still runs behind ngrok, no VPS.
- No integration with clinic software (Dentist Plus, IDENT).

## Tech stack

- Python, FastAPI, SQLite
- LLM through OpenRouter (gpt-4o-mini), function calling
- Voximplant VoxEngine (JavaScript), Management API
- Yandex SpeechKit ASR/TTS; in the LiveKit version Deepgram and ElevenLabs
- LiveKit Agents, SIP

## Running the current version

```bash
cd dental-ai-voximplant
pip install -r requirements.txt
cp .env.example .env             # OPENROUTER_API_KEY is enough for text mode
set -a; source .env; set +a
uvicorn server:app --reload --port 8000
```

Open `http://127.0.0.1:8000/dashboard` and use the text mode. For real calls, fill in
the `VOX_*` variables, expose the backend with `ngrok http 8000` and upload the
scenario to Voximplant. The step-by-step setup is in
[dental-ai-voximplant/README.md](dental-ai-voximplant/README.md) (Russian).

## Docs (Russian)

- [docs/product-and-costs.md](docs/product-and-costs.md) — how the product works and what a minute of conversation costs.
- [docs/voice-infra-comparison.md](docs/voice-infra-comparison.md) — LiveKit Cloud vs self-hosted LiveKit vs Voximplant vs MTS Exolve.
