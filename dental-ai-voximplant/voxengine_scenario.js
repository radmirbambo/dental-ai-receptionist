/**
 * voxengine_scenario.js — сценарий исходящего звонка AI-оператора (Voximplant).
 *
 * Этот код исполняется В ОБЛАКЕ Voximplant (их движок VoxEngine, JavaScript).
 * Роль: телефония + уши/рот. Мозг остаётся у нас: на каждый ход диалога сценарий
 * шлёт распознанный текст на наш бэкенд (/api/vox/turn), получает ответ и озвучивает.
 *
 * Схема:  телефон пациента ↔ Voximplant(этот сценарий: звонок, Yandex ASR/TTS)
 *                                  ↕ HTTP
 *                          наш FastAPI (LLM + слоты + запись + дашборд)
 *
 * Запускается из бэкенда через Management API (StartScenarios) с customData:
 *   { "lead_id": 1, "phone": "+79991234567", "name": "Иван",
 *     "backend_url": "https://ваш-бэкенд", "caller_id": "+7495..." }
 *
 * ВАЖНО: точные имена констант голосов/моделей (VoiceList…, ASRModelList…)
 * проверьте автодополнением в облачной IDE Voximplant — они периодически меняются.
 */

require(Modules.ASR);

// ---------- параметры запуска ----------
// ВАЖНО: customData читаем ВНУТРИ AppEvents.Started, а не при загрузке скрипта:
// платформа кладёт данные (SetCustomData) на миллисекунды позже загрузки — при
// чтении на верхнем уровне получим пустоту (проверено логом сессии).
let LEAD_ID, PHONE, NAME, BACKEND, CALLER_ID;

let call = null;
let asr = null;
let agentSpeaking = false;   // говорит ли сейчас агент (для barge-in)
let hangupAfterSpeech = false;

// ---------- помощники ----------
function log(msg) { Logger.write("[dental] " + msg); }

// POST на наш бэкенд; cb(получает распарсенный JSON или null)
function post(path, body, cb) {
  Net.httpRequestAsync(BACKEND + path, {
    method: "POST",
    headers: ["Content-Type: application/json"],
    postData: JSON.stringify(body),
  }).then(res => {
    try { cb(res && res.code === 200 ? JSON.parse(res.text) : null); }
    catch (e) { log("bad json: " + e); cb(null); }
  }).catch(e => { log("http error: " + e); cb(null); });
}

function notifyStatus(status, summary) {
  post("/api/vox/event", { lead_id: LEAD_ID, status: status, summary: summary || "" }, () => {});
}

// Озвучить фразу (Yandex-голос Алёна). После окончания — снова слушаем.
function say(text) {
  agentSpeaking = true;
  // Имя голоса проверить в IDE: VoiceList.Yandex.Neural.ru_RU_alena (или актуальное)
  call.say(text, { language: VoiceList.Yandex.Neural.ru_RU_alena });
}

// ---------- старт ----------
VoxEngine.addEventListener(AppEvents.Started, (e) => {
  // данные запуска: сначала пробуем из события, затем из customData()
  const raw = (e && e.customData) || VoxEngine.customData() || "{}";
  let data = {};
  try { data = JSON.parse(raw); } catch (err) { log("bad customData: " + raw); }
  LEAD_ID = data.lead_id;
  PHONE = data.phone;
  NAME = data.name || "";
  BACKEND = (data.backend_url || "").replace(/\/+$/, "");
  CALLER_ID = data.caller_id;

  if (!PHONE || !BACKEND) { log("no phone/backend in customData: " + raw); VoxEngine.terminate(); return; }
  log("dialing " + PHONE + " (lead " + LEAD_ID + ")");
  call = VoxEngine.callPSTN(PHONE, CALLER_ID);

  call.addEventListener(CallEvents.Connected, onConnected);
  call.addEventListener(CallEvents.Failed, e => { notifyStatus("no_answer", "Не дозвонились (" + e.code + ")"); VoxEngine.terminate(); });
  call.addEventListener(CallEvents.Disconnected, () => { log("hangup"); VoxEngine.terminate(); });
  // окончание каждой TTS-фразы
  call.addEventListener(CallEvents.PlaybackFinished, () => {
    agentSpeaking = false;
    if (hangupAfterSpeech) { call.hangup(); }
  });
});

// ---------- трубку взяли ----------
function onConnected() {
  notifyStatus("calling");

  // Распознавание: непрерывное, русская модель Yandex (имя модели проверить в IDE)
  asr = VoxEngine.createASR({
    profile: ASRProfileList.Yandex.ru_RU,   // провайдер+язык; если IDE не знает — наберите ASRProfileList. и выберите русский
    singleUtterance: false,
  });
  call.sendMediaTo(asr);

  // barge-in: пациент начал говорить, пока говорит агент -> обрываем озвучку
  asr.addEventListener(ASREvents.CaptureStarted, () => {
    if (agentSpeaking) { call.stopPlayback(); agentSpeaking = false; }
  });

  // финальный распознанный текст -> ход диалога на бэкенде
  asr.addEventListener(ASREvents.Result, e => {
    const text = (e.text || "").trim();
    if (!text) return;
    log("user: " + text);
    post("/api/vox/turn", { lead_id: LEAD_ID, text: text }, resp => {
      if (!resp || !resp.reply) { say("Извините, я вас не расслышала. Повторите, пожалуйста."); return; }
      log("agent: " + resp.reply);
      if (resp.end_call) hangupAfterSpeech = true;   // попрощаться и положить трубку
      say(resp.reply);
    });
  });

  // Приветствие — фиксированной фразой, мгновенно (мозг для него не нужен)
  say("Здравствуйте" + (NAME ? ", " + NAME : "") +
      "! Меня зовут Алиса, я из клиники «Улыбка», звоню по вашей заявке. Вам сейчас удобно говорить?");
}
