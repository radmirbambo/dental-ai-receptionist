/**
 * voxengine_inbound.js — ВХОДЯЩИЙ звонок: пациент звонит сам, агент берёт трубку.
 *
 * Зачем: работает на тестовом номере Voximplant БЕЗ верификации аккаунта.
 * Демо-флоу: звоните на тестовый номер -> вводите код доступа -> отвечает Алёна.
 *
 * Настройка (см. README, раздел «Входящий режим»):
 *   1) Впишите ниже ваш ngrok-адрес в BACKEND (меняется при перезапуске ngrok!).
 *   2) Создайте в приложении сценарий dental-inbound с этим кодом.
 *   3) Создайте правило inbound (шаблон .*) с этим сценарием и поставьте его
 *      ПЕРВЫМ в списке правил (исходящее правило работает по rule_id и не пострадает).
 *   4) Раздел «Тестовые номера» -> взять номер, привязать к приложению dental.
 */

require(Modules.ASR);

// ВАЖНО: ваш публичный адрес бэкенда (ngrok). Без слэша на конце.
const BACKEND = "https://YOUR-SUBDOMAIN.ngrok-free.app";

let call = null;
let asr = null;
let agentSpeaking = false;
let hangupAfterSpeech = false;
let LEAD_ID = null;
let callerPhone = "";

function log(msg) { Logger.write("[dental-in] " + msg); }

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

function say(text) {
  agentSpeaking = true;
  call.say(text, { language: VoiceList.Yandex.Neural.ru_RU_alena });
}

// ---------- входящий вызов ----------
VoxEngine.addEventListener(AppEvents.CallAlerting, (e) => {
  call = e.call;
  callerPhone = e.callerid || "";
  log("incoming from " + callerPhone);

  call.addEventListener(CallEvents.Connected, onConnected);
  call.addEventListener(CallEvents.Disconnected, () => { log("hangup"); VoxEngine.terminate(); });
  call.addEventListener(CallEvents.PlaybackFinished, () => {
    agentSpeaking = false;
    if (hangupAfterSpeech) { call.hangup(); }
  });

  call.answer();   // берём трубку
});

function onConnected() {
  // Заводим лид на бэкенде по номеру звонящего (появится на дашборде).
  post("/api/vox/inbound", { phone: callerPhone }, resp => {
    if (resp && resp.lead_id) { LEAD_ID = resp.lead_id; log("lead " + LEAD_ID); }
  });

  asr = VoxEngine.createASR({
    profile: ASRProfileList.Yandex.ru_RU,   // провайдер+язык; если IDE не знает — наберите ASRProfileList. и выберите русский
    singleUtterance: false,
  });
  call.sendMediaTo(asr);

  // barge-in
  asr.addEventListener(ASREvents.CaptureStarted, () => {
    if (agentSpeaking) { call.stopPlayback(); agentSpeaking = false; }
  });

  asr.addEventListener(ASREvents.Result, (e) => {
    const text = (e.text || "").trim();
    if (!text) return;
    log("user: " + text);
    // если lead ещё не создан (гонка) — бэкенд найдёт/создаст его по номеру
    post("/api/vox/turn", { lead_id: LEAD_ID, phone: callerPhone, text: text }, resp => {
      if (!resp || !resp.reply) { say("Извините, я вас не расслышала. Повторите, пожалуйста."); return; }
      log("agent: " + resp.reply);
      if (resp.end_call) hangupAfterSpeech = true;
      say(resp.reply);
    });
  });

  say("Здравствуйте! Стоматология «Улыбка», меня зовут Алиса. Чем могу помочь?");
}
