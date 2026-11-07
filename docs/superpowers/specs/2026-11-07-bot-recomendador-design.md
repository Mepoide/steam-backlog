# Bot recomendador de backlog (Telegram) — Diseño

**Fecha:** 2026-11-07 · **Estado:** aprobado en conversación, pendiente de plan de implementación

## Propósito

El usuario quiere **descubrir + decidir**: ver su backlog (403 juegos, propios +
Family Sharing) enriquecido con género, duración y valoración, y que algo le
ayude a elegir qué jugar. Uso típico: desde el móvil, en cualquier sitio
("tengo 2 horas esta tarde, ¿qué juego?"). Éxito = decirle al bot cuánto
tiempo/humor tiene y recibir 2-3 opciones de su backlog con el motivo, y poder
marcar estados desde el chat.

**Decisiones de discovery:**
- Interfaz: **bot de Telegram primero**, web después (fuera de alcance v1).
- Hosting: **homelab, Mac mini M4**. Sin puertos abiertos (long polling).
- Recomendación **híbrida**: reglas/filtros deterministas eligen candidatos;
  el LLM solo interpreta lenguaje natural y redacta. Además, recomendación
  **tipo Netflix** no conversacional: perfil de gusto calculado desde horas
  jugadas × tags.
- Arquitectura: **monolito modular** (enfoque A). Se descartó FastAPI desde
  el día 1 (YAGNI) y el bot 100 % agente LLM (caro, impredecible).
- Usuario único (chat_id en allowlist), aunque la biblioteca incluya Family
  Sharing.

## Reglas que se mantienen

- Solo se versiona `backlog.csv` (appid, name, estado, prioridad, notas).
  Sin SteamIDs, horas ni datos crudos en git; `data/` y `.env` ignorados.
- Secretos nuevos en `.env`: `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`.
- El pipeline de export (`export.py`, `sync_backlog.py`) sigue solo-stdlib.
  El bot y el enriquecimiento sí usan dependencias (venv + `requirements.txt`).

## Componentes

### 1. `src/enrich.py` — enriquecimiento (deja de ser stub)

Genera `data/enriched/games.json` (dict por appid) con:

| Campo | Fuente | Notas |
|---|---|---|
| géneros, categorías, metacritic | Steam store `appdetails` | Sin clave. |
| tags con votos | API de SteamSpy | Gratuita, sin clave. |
| % reseñas positivas, nº reseñas | Steam `appreviews` (query_summary) | Sin clave. |
| duración (main, main+extra) | HowLongToBeat | **API no oficial**: puede romperse sin aviso (mismo riesgo asumido que `IFamilyGroupsService`). Fallo ⇒ campo vacío, nunca aborta. |

- **Incremental con caché**: solo pide appids sin entrada (o con `--refresh`).
- Rate limit ~1 req/s por servicio; tolerante a fallos por juego (registra y
  sigue).
- Entrada: appids de `data/raw/steam_library.csv`.

### 2. `src/profile.py` — perfil de gusto (la parte Netflix)

- Cruza horas jugadas (`data/raw/`) con tags/géneros de `games.json`.
- Vector de afinidad por tag: peso = `log(1 + horas)` sumado sobre los juegos
  jugados que tienen ese tag (el log evita que 300 h de un juego lo aplasten
  todo). Plus de recencia a lo jugado en los últimos meses.
- Puntúa cada juego **pendiente**: afinidad de tags (similitud con el vector)
  + calidad (% reseñas positivas, suavizado por nº de reseñas) + un término de
  variedad/novedad.
- Cada recomendación guarda su explicación: el juego jugado más cercano por
  solape de tags → "porque jugaste **X** (comparten A, B)".
- Salida precalculada a diario: `data/enriched/profile.json` (vector) y
  `data/enriched/scores.json` (puntuación + motivo por appid pendiente).
- Determinista, sin LLM.

### 3. `src/recommender.py` — módulo puro de recomendación

- No sabe nada de Telegram ni del LLM (la web futura lo reutiliza tal cual).
- Entrada: filtros estructurados — tiempo disponible (→ duración HLTB),
  géneros/tags incluidos o excluidos, estado de ánimo ya mapeado a tags,
  nº de resultados.
- Salida: top-N de pendientes con puntuación y motivo ("porque jugaste…",
  duración, valoración).
- El LLM **nunca puntúa ni elige**: solo traduce frase libre → filtros, y
  redacta la respuesta final.

### 4. `src/bot.py` — bot de Telegram

- Librería `python-telegram-bot`, **long polling** (cero puertos abiertos).
- Solo responde al chat_id del usuario (allowlist en `.env`).
- Comandos:
  - `/top` — filas tipo Netflix: "para ti", "sesión corta (<5 h)",
    "joyas valoradas (>90 %)".
  - `/recomienda [tiempo] [género]` — atajo estructurado.
  - Texto libre ("algo tranquilo, estoy quemado") — API de Claude (modelo
    Haiku) lo convierte en filtros, el recomendador elige, Claude redacta.
    Coste: céntimos/consulta. Si la API falla, degradar a `/recomienda`.
  - `/marcar <juego> <estado>` — edita `backlog.csv`
    (pendiente|jugando|terminado|abandonado|descartado) con búsqueda difusa
    del nombre y confirmación.
  - `/buscar <texto>` — consulta el backlog.
- Errores: siempre responde algo; los fallos van al log, no al chat.

### 5. Operación (Mac mini M4)

- Clon del repo en el Mac mini.
- `launchd` diario: `export.py → sync_backlog.py → enrich.py → profile.py`;
  si `backlog.csv` cambió, auto-commit + push (también tras `/marcar`).
- El bot corre como servicio `launchd` con reinicio automático (`KeepAlive`).
- Si caduca `STEAM_WEBAPI_TOKEN` (~24 h), el export sigue con la biblioteca
  propia y el log lo avisa; renovar es manual (limitación conocida).

## Testing

- `profile.py` y `recommender.py` (la lógica con chicha) con tests unitarios
  sobre fixtures pequeñas: afinidad, amortiguación log, filtros de duración,
  explicaciones.
- `enrich.py`: tests del parseo de cada fuente con respuestas JSON grabadas.
- Bot: handlers finos; probar el mapeo comando→llamada al recomendador, sin
  red.

## Fuera de alcance (v1)

- Web/PWA (el diseño modular la deja preparada: `recommender.py` + datos).
- Multiusuario / familia.
- Notificaciones proactivas (digest semanal) — fácil de añadir luego.
