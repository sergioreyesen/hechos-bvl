# Guía de despliegue — Hechos BVL (todo gratis)

## 0. Antes de nada
Confirma que ya **revocaste** la API key que pegaste en el chat y creaste una
nueva en aistudio.google.com. Esa nueva key nunca se pega en ningún chat ni
se escribe dentro de ningún archivo del proyecto — va directo a GitHub como
"Secret" (paso 3).

## 1. Sube la carpeta completa a GitHub
Sube TODOS los archivos y carpetas tal cual están (`index.html`, `manifest.json`,
`sw.js`, los íconos, `data/`, `scripts/`, `.github/`) al repositorio que ya
creaste (`hechos-bvl`), respetando la estructura de carpetas.

## 2. Activa GitHub Pages
Settings → Pages → Branch: `main` → Save.
(Igual que hiciste antes — esto no cambia.)

## 3. Agrega tus dos claves como Secrets (no como archivos)
Ve a: **Settings → Secrets and variables → Actions → New repository secret**

Crea dos secrets:
| Nombre exacto | Valor |
|---|---|
| `GEMINI_API_KEY` | tu nueva API key de Google AI Studio |
| `NTFY_TOPIC` | `sergio-hechos-bvl-x7k2` |

GitHub las guarda cifradas — ni tú las vuelves a ver completas, ni yo puedo
verlas nunca.

## 4. Activa el workflow automático
Ve a la pestaña **Actions** de tu repositorio. Si aparece un aviso para
habilitar Actions, acéptalo. Ahí verás el workflow "Actualizar Hechos BVL".

## 5. Pruébalo manualmente la primera vez
En la pestaña Actions → click en "Actualizar Hechos BVL" → botón
**"Run workflow"** (arriba a la derecha) → Run workflow.
Espera 1-2 minutos y revisa los logs — ahí vas a ver cuántos hechos
encontró y si hubo errores.

## 6. Verifica que llegó la notificación
Si el paso 5 encontró hechos nuevos, debería llegarte una notificación
push a la app ntfy en tu celular (o la puedes ver en ntfy.sh/sergio-hechos-bvl-x7k2
desde el navegador).

## 7. Revisa la app
Abre tu link de GitHub Pages (`https://tu-usuario.github.io/hechos-bvl/`)
— ya debería mostrar datos reales en vez de los de ejemplo.

## De aquí en adelante
El workflow corre solo, dos veces al día en horario de mercado peruano
(8:30am y 4:30pm), sin que hagas nada. Cada vez que encuentre un hecho
nuevo: lo resume, lo guarda, y te notifica.

## Si algo no funciona a la primera
Es normal — el scraper depende de que el patrón de texto de la página de
la SMV coincida exactamente con lo que detecté hoy. Si los logs del paso 5
muestran "0 hechos parseados" o un error, cópiame el mensaje del log y lo
ajustamos juntos.

## Pendiente para una siguiente iteración (no incluido todavía)
- Historial retroactivo (antes de que actives el workflow, no hay forma
  de "traer" el pasado — el historial se construye día a día desde que
  lo enciendes).
- Noticias de prensa relacionadas (por ahora la app está lista para
  mostrarlas, pero el scraper de RSS de medios aún no está conectado).
