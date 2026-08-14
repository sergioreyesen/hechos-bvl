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
GEMINI_MODEL = "gemini-2.0-flash"
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


def html_to_text(chunk_html):
    """Convierte un trozo de HTML a texto plano legible, quitando etiquetas."""
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', chunk_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    # Quita cualquier resto de etiqueta que haya quedado sin cerrar al
    # final del trozo (por ejemplo un "<a" cortado justo en el borde
    # del bloque, antes de su atributo href).
    text = re.sub(r'<[^<>]*$', '', text)
    text = re.sub(r'[ \t\r\n]+', ' ', text).strip()
    return text


def parse_hechos(raw_html):
    """
    Estrategia en 2 pasos (mas tolerante a cambios pequenos de HTML):
      1) Encuentra todos los enlaces a documento.aspx?vidDoc={GUID} en el HTML
         crudo (ahi SI necesitamos las etiquetas, porque el link vive en un
         atributo href).
      2) El trozo de HTML entre un enlace y el anterior es "el bloque" de ese
         hecho de importancia. Convertimos ESE trozo a texto plano y ahi
         buscamos empresa / expediente / fecha / tipo / sector.
    """
    items = []

    link_pattern = re.compile(
        r'href=["\']([^"\']*documento\.aspx\?vidDoc=\{([0-9A-F\-]+)\})["\']',
        re.IGNORECASE,
    )
    matches = list(link_pattern.finditer(raw_html))

    if not matches:
        return items

    sector_names_pattern = '|'.join(
        re.escape(k) for k in sorted(SECTOR_MAP.keys(), key=len, reverse=True)
    )

    block_start = 0
    for m in matches:
        # Usamos SOLO hasta el inicio del link (no su final) para el texto,
        # así nunca queda una etiqueta <a ...> a medio cerrar mezclada
        # con el contenido visible del bloque.
        block_html = raw_html[block_start:m.start()]
        pdf_url = m.group(1)
        block_start = m.end()

        text = html_to_text(block_html)

        header_match = re.search(
            r'([A-ZÁÉÍÓÚÑ0-9\.\-,&\s]{4,150}?)\s*EXP\.\s*(\d+)\s*DEL\s*'
            r'(\d{2}/\d{2}/\d{4}\s*\d{2}:\d{2})',
            text,
            re.IGNORECASE,
        )
        if not header_match:
            continue

        empresa = header_match.group(1).strip(" \t\n|,-")
        exp = header_match.group(2)
        fecha = header_match.group(3).strip()
        rest = text[header_match.end():]

        tipo_match = re.match(r'^(.*?)(?:Fecha de acuerdo|EMPRESAS)', rest)
        tipo = tipo_match.group(1).strip(" :.-") if tipo_match else "Hecho de Importancia"

        # Solo reconocemos sectores de nuestra lista conocida (SECTOR_MAP),
        # así evitamos que la captura se coma letras del texto siguiente.
        sector_match = re.search(
            rf'EMPRESAS EMISORAS\s*\|\s*({sector_names_pattern})',
            rest,
            re.IGNORECASE,
        )
        categoria_smv = sector_match.group(1).strip().upper() if sector_match else "OTROS"

        snippet = ""
        if sector_match:
            after = rest[sector_match.end():].strip(" .-")
            if len(after) > 8:
                snippet = after[:400]

        sector = SECTOR_MAP.get(categoria_smv.upper().strip(), "otros")

        items.append({
            "exp": exp,
            "fecha": fecha,
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
