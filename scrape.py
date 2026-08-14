"""
Scraper diario de "Hechos de Importancia" — SMV Perú
------------------------------------------------------
Qué hace:
  1. Descarga la página pública del día (sin login, sin JS) con los hechos
     de importancia registrados hoy.
  2. Extrae: empresa, N° de expediente, fecha/hora, tipo de hecho,
     sector oficial SMV, descripción corta (si existe) y enlace al PDF.
  3. Compara contra lo que ya teníamos guardado (data/hechos.json) y
     detecta cuáles son NUEVOS.
  4. Para cada hecho nuevo: le pide a Gemini un resumen breve en lenguaje
     simple + lo clasifica en uno de nuestros rubros de la app.
  5. Guarda todo en data/hechos.json (con historial acumulado).
  6. Envía una notificación push a ntfy.sh por cada hecho nuevo.

Nota honesta: el parseo del HTML de la SMV se hizo con expresiones
regulares basadas en el patrón de texto observado en la página pública.
Si la SMV cambia el diseño de su sitio, este script puede necesitar un
ajuste — está escrito para fallar de forma visible (no silenciosa) si
el patrón deja de coincidir, así es fácil detectarlo en los logs de
GitHub Actions.
"""

import os
import re
import json
import html
import requests
from datetime import datetime, timezone

SMV_URL = "https://www.smv.gob.pe/SIMV/Frm_hechosdeImportanciaDia"
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "hechos.json")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}" if NTFY_TOPIC else None

# Mapeo de sectores oficiales SMV -> rubros simplificados de la app
SECTOR_MAP = {
    "MINERAS": "mineria",
    "BANCOS Y FINANCIERAS": "banca",
    "FINANCIERAS": "banca",
    "SEGUROS": "seguros",
    "INDUSTRIALES": "industrial",
    "MANUFACTURA": "industrial",
    "SANEAMIENTO": "industrial",
    "COMERCIO": "retail",
    "DIVERSAS": "otros",
    "SERVICIOS": "otros",
    "ELECTRICIDAD": "energia",
    "ENERGIA": "energia",
    "PETROLEO Y GAS": "energia",
    "AGROPECUARIA": "otros",
    "FONDOS DE INVERSION": "otros",
    "TELECOMUNICACIONES": "telecom",
    "CONSTRUCCION": "industrial",
}

STAMP_BY_SECTOR = {
    "mineria": "oro",
    "banca": "azul",
    "retail": "rojo",
    "industrial": "verde",
    "energia": "oro",
    "telecom": "azul",
    "seguros": "verde",
    "otros": "verde",
}


def fetch_html():
    resp = requests.get(SMV_URL, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (compatible; HechosBVL-personal-app/1.0)"
    })
    resp.raise_for_status()
    return resp.text


def parse_hechos(raw_html):
    """
    Extrae cada bloque de hecho de importancia del HTML crudo usando
    los marcadores de texto observados en la página pública de la SMV:
    'EXP. <num> DEL <fecha>', 'Fecha de acuerdo:', 'EMPRESAS EMISORAS | <SECTOR>',
    y el enlace a documento.aspx?vidDoc={GUID}
    """
    items = []

    # Cada fila trae: nombre empresa, luego "EXP. NNNNNNNNN DEL DD/MM/YYYY HH:MM"
    pattern = re.compile(
        r'([A-ZÁÉÍÓÚÑ0-9\.\-,&\s]{4,120}?)\s*'
        r'EXP\.\s*(\d+)\s*DEL\s*(\d{2}/\d{2}/\d{4}\s*\d{2}:\d{2})'
        r'(.{0,600}?)'
        r'documento\.aspx\?vidDoc=\{([0-9A-F\-]+)\}',
        re.IGNORECASE | re.DOTALL,
    )

    for m in pattern.finditer(raw_html):
        empresa_raw, exp, fecha, middle, guid = m.groups()
        empresa = html.unescape(empresa_raw).strip(" \t\n|,-")
        middle_clean = html.unescape(re.sub(r'<[^>]+>', ' ', middle))
        middle_clean = re.sub(r'\s+', ' ', middle_clean).strip()

        # tipo de hecho: primera línea de texto después de la fecha (antes de "Fecha de acuerdo")
        tipo_match = re.match(r'^(.*?)(?:Fecha de acuerdo|EMPRESAS)', middle_clean)
        tipo = tipo_match.group(1).strip(" :.-") if tipo_match else "Hecho de Importancia"

        # sector oficial SMV: texto después del último "|"
        sector_match = re.search(r'EMPRESAS EMISORAS\s*\|\s*([A-ZÁÉÍÓÚÑ\s]+)', middle_clean)
        categoria_smv = sector_match.group(1).strip() if sector_match else "OTROS"

        # snippet/descripción: lo que queda después del bloque de sector, si hay
        snippet = ""
        if sector_match:
            after = middle_clean[sector_match.end():].strip(" .-")
            if len(after) > 8:
                snippet = after

        pdf_url = f"https://www.smv.gob.pe/ConsultasP8/documento.aspx?vidDoc={{{guid}}}"
        sector = SECTOR_MAP.get(categoria_smv.upper().strip(), "otros")

        items.append({
            "exp": exp,
            "fecha": fecha.strip(),
            "empresa": empresa,
            "tipo": tipo,
            "categoria_smv": categoria_smv,
            "snippet": snippet,
            "pdf_url": pdf_url,
            "sector": sector,
        })

    return items


def load_existing():
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_all(items):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    # ordenar por fecha descendente y recortar a 12 meses
    items_sorted = sorted(
        items,
        key=lambda x: datetime.strptime(x["fecha"], "%d/%m/%Y %H:%M"),
        reverse=True,
    )
    cutoff = datetime.now() - __import__("datetime").timedelta(days=365)
    items_sorted = [
        x for x in items_sorted
        if datetime.strptime(x["fecha"], "%d/%m/%Y %H:%M") >= cutoff
    ]
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(items_sorted, f, ensure_ascii=False, indent=2)


def summarize_with_gemini(item):
    """Pide a Gemini un resumen breve en lenguaje simple. Si falla, usa un
    resumen de respaldo basado en los datos crudos (nunca deja el campo vacío)."""
    fallback = item["snippet"] or f'{item["empresa"]} presentó: {item["tipo"]}.'

    if not GEMINI_API_KEY:
        return fallback

    prompt = f"""Eres un asistente que resume comunicados oficiales de la Superintendencia
del Mercado de Valores de Perú (SMV) para un inversionista individual no especializado.

Datos del hecho de importancia:
- Empresa: {item['empresa']}
- Tipo de hecho: {item['tipo']}
- Sector: {item['categoria_smv']}
- Texto/descripción disponible: {item['snippet'] or '(sin descripción adicional, usa el tipo de hecho)'}

Escribe un resumen de 2 a 3 líneas en español, en lenguaje simple y neutral,
SIN opinar sobre el impacto en el precio de la acción ni dar recomendaciones
de inversión. Responde SOLO con el texto del resumen, sin comillas, sin
markdown, sin preámbulo."""

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text if text else fallback
    except Exception as e:
        print(f"[WARN] Gemini falló para EXP {item['exp']}: {e}")
        return fallback


def notify_ntfy(item):
    if not NTFY_URL:
        return
    try:
        requests.post(
            NTFY_URL,
            data=item["resumen"].encode("utf-8"),
            headers={
                "Title": f"{item['empresa']} · {item['tipo']}".encode("utf-8"),
                "Priority": "default",
                "Tags": "bank",
            },
            timeout=15,
        )
    except Exception as e:
        print(f"[WARN] No se pudo notificar EXP {item['exp']}: {e}")


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Iniciando scraper SMV…")

    raw_html = fetch_html()
    scraped = parse_hechos(raw_html)
    print(f"Encontrados {len(scraped)} hechos en la página del día.")

    if len(scraped) == 0:
        print("[WARN] 0 hechos parseados. Es posible que la SMV haya cambiado "
              "el formato de la página, o que hoy no haya hechos aún. Revisar.")

    existing = load_existing()
    existing_exps = {x["exp"] for x in existing}

    nuevos = [x for x in scraped if x["exp"] not in existing_exps]
    print(f"{len(nuevos)} hechos nuevos respecto a lo ya guardado.")

    for item in nuevos:
        item["resumen"] = summarize_with_gemini(item)
        item["stamp"] = STAMP_BY_SECTOR.get(item["sector"], "verde")
        item["fetched_at"] = datetime.now(timezone.utc).isoformat()
        notify_ntfy(item)

    save_all(existing + nuevos)
    print("Listo. data/hechos.json actualizado.")


if __name__ == "__main__":
    main()
