"""
vox_dispatch.py — запуск исходящего звонка через Voximplant Management API.

Бэкенд вызывает start_outbound_call(lead) -> Voximplant запускает наш сценарий
(voxengine_scenario.js), привязанный к правилу (rule), и передаёт ему customData.

Нужны переменные окружения:
    VOX_ACCOUNT_ID  — id аккаунта (в панели Voximplant)
    VOX_API_KEY     — API-ключ аккаунта
    VOX_RULE_ID     — id правила приложения, к которому привязан сценарий
    VOX_CALLER_ID   — подтверждённый/арендованный номер, с которого звоним
    PUBLIC_BACKEND_URL — публичный URL этого бэкенда (сценарий будет его дёргать)
"""

import json
import os
import urllib.parse
import urllib.request

API = "https://api.voximplant.com/platform_api/StartScenarios/"


def start_outbound_call(lead: dict) -> dict:
    params = {
        "account_id": os.environ["VOX_ACCOUNT_ID"],
        "api_key": os.environ["VOX_API_KEY"],
        "rule_id": os.environ["VOX_RULE_ID"],
        "script_custom_data": json.dumps({
            "lead_id": lead["id"],
            "phone": lead["phone"],
            "name": lead["name"],
            "backend_url": os.environ["PUBLIC_BACKEND_URL"],
            "caller_id": os.environ["VOX_CALLER_ID"],
        }, ensure_ascii=False),
    }
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("result"):
        raise RuntimeError(f"StartScenarios failed: {resp}")
    return resp
