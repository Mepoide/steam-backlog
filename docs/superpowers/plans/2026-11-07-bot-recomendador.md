# Bot recomendador de backlog — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enriquecer la biblioteca de Steam, calcular un perfil de gusto por horas × tags y servir recomendaciones del backlog por un bot de Telegram que corre en el Mac mini.

**Architecture:** Monolito modular en `src/`: módulos puros (`library`, `enrich_sources`, `taste`, `recommender`, `commands`) que no saben de Telegram, y capas finas encima (`enrich.py`/`taste.py` como CLI diarias, `llm.py` para Claude, `bot.py` para Telegram). Los datos derivados viven en `data/enriched/` (ignorado); solo `backlog.csv` se versiona.

**Tech Stack:** Python ≥ 3.11, pytest, python-telegram-bot ≥ 21 (async, long polling), anthropic SDK, howlongtobeatpy, python-dotenv; urllib (stdlib) para las APIs de Steam/SteamSpy; launchd en macOS.

**Spec:** `docs/superpowers/specs/2026-11-07-bot-recomendador-design.md`

## Global Constraints

- **El repo es PÚBLICO.** Nunca commitear claves, tokens, SteamIDs, horas de juego ni nombres personales. Las fixtures de tests son sintéticas o respuestas públicas de la tienda.
- Solo `backlog.csv` se versiona como dato (appid, name, estado, prioridad, notas). `data/` y `.env` siguen ignorados.
- `src/export.py` y `src/sync_backlog.py` siguen siendo **solo stdlib**. El resto usa el venv (`requirements.txt`).
- Estados válidos: `pendiente|jugando|terminado|abandonado|descartado`.
- Secretos nuevos en `.env`: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`.
- El LLM **nunca puntúa ni elige** juegos: solo texto libre → filtros, y una intro de ≤ 2 frases. Modelo: `claude-haiku-5-5`.
- Telegram por **long polling**; cero puertos abiertos.
- Todo texto visible para el usuario en español.
- Ejecutar desde la raíz del repo: `.venv/bin/python src/<x>.py`; tests con `.venv/bin/pytest`.
- Desviación de la spec: el perfil vive en `src/taste.py` (no `profile.py`, que taparía el módulo `profile` de la stdlib). Las salidas mantienen el nombre de la spec: `data/enriched/profile.json` y `scores.json`.
- Commits en español, terminados en `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Los commits automáticos del bot o del job diario no llevan esa línea.

## Review Focus

1. **Juegos sin duración HLTB** con un filtro de tiempo: se excluyen del resultado sin romper nada y en los listados se muestran como `? h` (tests en la tarea 5).
2. **Respuestas raras de las fuentes**: `success: false` de la tienda (juegos retirados), `"tags": []` de SteamSpy (lista en vez de dict) y búsqueda en HLTB sin resultados. Dan campos vacíos y **no** se reintentan cada día; solo se reintentan los errores de red o HTTP (tests en las tareas 2 y 3).
3. **Cero resultados**: si los filtros no dejan nada, `/recomienda` lo dice y sugiere relajarlos; el texto libre recurre a "Para ti" (tests en las tareas 5 y 7).
4. **`/marcar` ambiguo, sin coincidencia o con estado inválido**: nunca escribe en el backlog sin confirmación por botón y nunca marca otro juego (tests en la tarea 7).
5. **Nombres con ™/®, acentos y mayúsculas**: `ACE COMBAT™7` encuentra su entrada en HLTB, y `estrategia` / `rogue` encuentran `Estrategia` / `Roguelike` (tests en las tareas 2 y 5).

---

### Task 1: Base del proyecto — venv, config y `library`

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `src/config.py`, `src/library.py`, `tests/conftest.py`, `tests/test_library.py`

**Interfaces:**
- Produces:
  - `config`: `ROOT`, `RAW_CSV` (`data/raw/steam_library.csv`), `ENRICHED_DIR`, `GAMES_JSON`, `PROFILE_JSON`, `SCORES_JSON`, `PLAYTIME_CACHE` (`data/enriched/playtime_cache.json`), `BACKLOG_CSV`; `load_env() -> None` (python-dotenv sobre `ROOT/.env`, sin override); `require_env(name: str) -> str` (`SystemExit(f"Falta {name} en .env")` si está vacía).
  - `library`: `ESTADOS: frozenset[str]`; `read_backlog(path=BACKLOG_CSV) -> list[dict]`; `write_backlog(rows, path=BACKLOG_CSV) -> None` (atómico: tmp + `os.replace`, columnas `appid,name,estado,prioridad,notas`); `set_estado(appid: str, estado: str, path=BACKLOG_CSV) -> bool` (`ValueError` si el estado no es válido; `False` si el appid no existe); `@dataclass Playtime(hours: float, last_played: date | None)`; `load_playtime(raw_csv=RAW_CSV, cache_path=PLAYTIME_CACHE) -> dict[str, Playtime]`; `atomic_write_json(path, obj) -> None`.

- [ ] **Step 1: Prepara el entorno**

`requirements.txt`: `python-dotenv`, `howlongtobeatpy>=1.0`, `anthropic`, `python-telegram-bot>=21.0`, `pytest`. `pytest.ini`: `[pytest]` con `pythonpath = src` y `testpaths = tests`.

Run: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
Expected: termina sin errores (`.venv/` ya está en `.gitignore`).

- [ ] **Step 2: Escribe los tests que fallan** (`tests/conftest.py` define `write_raw(path, rows)`, que escribe un CSV con la cabecera de `export.py`)

```python
def test_backlog_roundtrip_preserves_order(tmp_path): ...  # write_backlog → read_backlog == rows (appid "10" antes que "5")
def test_set_estado_changes_only_target(tmp_path):         # True; el resto de filas idénticas
def test_set_estado_rejects_unknown_estado(tmp_path):      # pytest.raises(ValueError): set_estado("5", "ganado", p)
def test_set_estado_unknown_appid_returns_false(tmp_path):
def test_load_playtime_keeps_last_known_hours(tmp_path):
    # raw1: appid 1 (3.0 h, 2026-10-01) y 2 (5.0 h) → load_playtime → ambos
    # raw2: solo appid 1 (4.0 h)  — simula el token familiar caducado
    # → {"1": Playtime(4.0, ...), "2": Playtime(5.0, ...)}  (el 2 sale de la caché)
def test_load_playtime_empty_last_played_is_none(tmp_path):
```

- [ ] **Step 3: Ejecuta y comprueba que fallan** — Run: `.venv/bin/pytest tests/test_library.py -v` · Expected: FAIL (`ModuleNotFoundError: library`).

- [ ] **Step 4: Implementa `config.py` y `library.py`** según Interfaces. `load_playtime` fusiona la caché con el CSV actual (manda el CSV) y reescribe la caché.

- [ ] **Step 5: Ejecuta los tests** — Run: `.venv/bin/pytest -v` · Expected: PASS.

- [ ] **Step 6: Commit** — `git add requirements.txt pytest.ini src/config.py src/library.py tests/ && git commit -m "Base: venv, config y lectura de backlog/horas"`

---

### Task 2: Fuentes de enriquecimiento (`enrich_sources`)

**Files:**
- Create: `src/enrich_sources.py`, `tests/test_enrich_sources.py`, `tests/fixtures/` (`store_1145360.json`, `spy_1145360.json`, `reviews_1145360.json`)
- Modify: `.gitignore` (añadir `!tests/fixtures/*.json` al final)

**Interfaces:**
- Produces: `class SourceError(Exception)`, `class RateLimited(SourceError)`; parsers puros `parse_store(payload: dict, appid: str) -> dict` (`genres`, `categories`: listas de `description`; `metacritic`: `int | None`), `parse_spy(payload: dict) -> dict` (`tags: dict[str, int]`), `parse_reviews(payload: dict) -> dict` (`reviews_positive`, `reviews_total`; `SourceError` si `success != 1`), `clean_name(name: str) -> str`, `pick_hltb(name: str, entries: list) -> dict` (`hltb_main`, `hltb_extra`); fetchers `fetch_store(appid, name)`, `fetch_spy(appid, name)`, `fetch_reviews(appid, name)`, `fetch_hltb(appid, name)`, todos `-> dict` con los campos de su parser.
- URLs (User-Agent `steam-backlog`):
  - tienda: `https://store.steampowered.com/api/appdetails?appids={appid}&l=spanish&cc=es`
  - SteamSpy: `https://steamspy.com/api.php?request=appdetails&appid={appid}`
  - reseñas: `https://store.steampowered.com/appreviews/{appid}?json=1&language=all&purchase_type=all&num_per_page=0`
- Throttle por host dentro de los fetchers (clase `Throttle(interval)`): store.steampowered.com 1.5 s (tienda y reseñas comparten el mismo throttle), SteamSpy 1.0 s, HLTB 1.0 s. HTTP 429 → `RateLimited`; otros errores de red o HTTP → `SourceError`.

- [ ] **Step 1: Graba las fixtures** — guarda con `curl` las tres URLs para el appid 1145360 (Hades) en `tests/fixtures/`. Si alguna pasa de ~20 KB, recórtala a los campos que se usan.

- [ ] **Step 2: Escribe los tests que fallan**

```python
def test_parse_store_real_fixture():     # genres no vacío, todos str; metacritic int o None
def test_parse_store_delisted():          # parse_store({"1": {"success": False}}, "1") == {"genres": [], "categories": [], "metacritic": None}
def test_parse_spy_real_fixture():        # len(tags) > 5 y todos los valores int
def test_parse_spy_tags_as_list():        # parse_spy({"tags": []}) == {"tags": {}}   (Review Focus 2)
def test_parse_reviews_real_fixture():    # 0 <= reviews_positive <= reviews_total
def test_parse_reviews_failure_raises():  # parse_reviews({"success": 2}) → SourceError
def test_clean_name():                    # clean_name("ACE COMBAT™7: SKIES  UNKNOWN") == "ACE COMBAT7: SKIES UNKNOWN"  (Review Focus 5)
def test_pick_hltb_best_similarity():     # entradas falsas (SimpleNamespace game_name/similarity/main_story/main_extra); gana la de mayor similarity
def test_pick_hltb_below_threshold():     # similarity < 0.6 → {"hltb_main": None, "hltb_extra": None}
def test_pick_hltb_zero_times_are_none(): # main_story == 0 → None
```

- [ ] **Step 3: Ejecuta y comprueba que fallan** — Run: `.venv/bin/pytest tests/test_enrich_sources.py -v` · Expected: FAIL.

- [ ] **Step 4: Implementa `src/enrich_sources.py`** según Interfaces. `fetch_hltb` busca con `HowLongToBeat().search(clean_name(name))` (si devuelve `None`, se trata como lista vacía). Una búsqueda sin coincidencias da campos `None` y **no** es un error.

- [ ] **Step 5: Ejecuta los tests** — Expected: PASS. Comprueba además que `git status` muestra las fixtures como nuevas (no ignoradas).

- [ ] **Step 6: Commit** — `git add .gitignore src/enrich_sources.py tests/ && git commit -m "Fuentes de enriquecimiento: tienda, SteamSpy, reseñas y HLTB"`

---

### Task 3: `enrich.py` — orquestación incremental con caché

**Files:**
- Modify: `src/enrich.py` (sustituye el stub por completo)
- Test: `tests/test_enrich.py`

**Interfaces:**
- Consumes: los fetchers y `SourceError` / `RateLimited` (tarea 2); `config.RAW_CSV`, `GAMES_JSON`; `library.atomic_write_json`.
- Produces: el esquema de `games.json` (dict por appid str), que usan las tareas 4-7:
  `{"name", "genres": [], "categories": [], "metacritic": None, "tags": {}, "reviews_positive": None, "reviews_total": None, "hltb_main": None, "hltb_extra": None, "missing": []}`. `missing` lista las fuentes (`"store"|"spy"|"reviews"|"hltb"`) que **fallaron** y se reintentarán.
  `SOURCES = ("store", "spy", "reviews", "hltb")`; `enrich_all(games: dict[str, str], cache: dict, fetchers: dict[str, Callable[[str, str], dict]], refresh: bool = False, limit: int | None = None, save: Callable[[dict], None] = lambda c: None, save_every: int = 25) -> dict`. Una entrada se procesa si es nueva, si tiene `missing` no vacío (solo esas fuentes) o si `refresh` está activo (todas). `limit` cuenta juegos procesados.
  CLI: `python src/enrich.py [--refresh] [--limit N]` → lee los appids y nombres de `RAW_CSV`, guarda atómicamente cada 25 juegos y al final, e imprime `"✓ N juegos enriquecidos, M con fuentes pendientes"`.

- [ ] **Step 1: Escribe los tests que fallan** (fetchers falsos que cuentan llamadas; nada de red ni de sleep)

```python
def test_new_game_gets_all_sources_merged():      # entrada plana con los campos de las 4 fuentes, missing == []
def test_complete_cached_entry_not_refetched():   # 0 llamadas
def test_failed_source_marked_missing_and_retried_alone():
    # 1.ª pasada: spy lanza SourceError → "spy" in missing, tags == {}
    # 2.ª pasada: solo se llama a spy; missing == []
def test_rate_limited_source_skipped_for_rest_of_run():  # store lanza RateLimited con el juego 1 → no se llama con el 2; ambos con "store" in missing
def test_not_found_is_not_missing():              # hltb devuelve campos None sin excepción → "hltb" not in missing   (Review Focus 2)
def test_refresh_refetches_everything():
def test_limit_caps_processed_games():
def test_save_called_every_n_and_at_end():        # save_every=2, 5 juegos → 3 llamadas
```

- [ ] **Step 2: Ejecuta y comprueba que fallan** — Run: `.venv/bin/pytest tests/test_enrich.py -v` · Expected: FAIL.
- [ ] **Step 3: Implementa `enrich_all` y `main()`** en `src/enrich.py` según Interfaces.
- [ ] **Step 4: Ejecuta los tests** — Expected: PASS.
- [ ] **Step 5: Smoke test real** — Run: `.venv/bin/python src/enrich.py --limit 5` · Expected: termina, y `data/enriched/games.json` tiene 5 entradas con géneros y tags no vacíos en al menos 3 de ellas.
- [ ] **Step 6: Commit** — `git commit -am "enrich.py: enriquecimiento incremental con caché y reintentos"` (añade antes el test nuevo con `git add tests/test_enrich.py`).

---

### Task 4: `taste.py` — perfil de gusto y puntuaciones (la parte Netflix)

**Files:**
- Create: `src/taste.py`, `tests/test_taste.py`
- Modify: `tests/conftest.py` — añade `make_game(name="G", tags=None, genres=None, hltb_main=None, pos=None, total=None) -> dict` (entrada completa según el esquema de la tarea 3, con `missing=[]`) y `make_backlog(*rows: tuple[str, str, str]) -> list[dict]` (appid, name, estado).

**Interfaces:**
- Consumes: el esquema de `games.json`; `library.Playtime`, `load_playtime`, `read_backlog`; `config.GAMES_JSON`, `PROFILE_JSON`, `SCORES_JSON`.
- Produces:
  - `tag_vector(entry: dict, top_n: int = 10) -> dict[str, float]`: los 10 tags con más votos, normalizados por el máximo (el top vale 1.0). Si no hay tags, usa los géneros con peso 1.0.
  - `build_profile(games, playtime, today: date) -> dict[str, float]`: suma sobre los juegos con horas > 0 de `log1p(horas) × recencia × tag_vector`; recencia = 1.5 si `last_played >= today - 180 días`, si no 1.0. Al final normaliza a norma L2 = 1.
  - `cosine(a: dict, b: dict) -> float`; `quality(entry) -> float` = `(pos + 0.75 × 50) / (total + 50)`, o 0.75 si no hay reseñas.
  - `score_pending(games, playtime, backlog, profile) -> dict[str, dict]`: solo juegos con estado `pendiente`. Cada uno da `{"score", "sim", "quality", "anchor_appid", "anchor_name", "shared_tags"}` con `score = 0.7 × sim + 0.3 × quality`. El ancla es el juego jugado que maximiza `cosine(tag_vector(jugado), tag_vector(pendiente)) × log1p(horas)`. `shared_tags` son los ≤ 2 tags comunes con mayor producto de pesos. Si no hay ningún tag común, el ancla y el nombre son `None` y `shared_tags = []`.
  - CLI: `python src/taste.py` escribe `PROFILE_JSON` y `SCORES_JSON` (vía `atomic_write_json`) e imprime `"✓ perfil con N tags, M pendientes puntuados"`.

- [ ] **Step 1: Escribe los tests que fallan**

```python
def test_tag_vector_normalizes_and_caps():   # {"A":100,"B":50} → {"A":1.0,"B":0.5}; 15 tags → 10
def test_tag_vector_falls_back_to_genres():
def test_profile_log_damping():              # X: 300 h tag A; Y: 10 h tag B → p["A"]/p["B"] ≈ log1p(300)/log1p(10)
def test_profile_recency_boost():            # mismas horas, uno hace 30 días y otro hace 400 → ratio 1.5
def test_unplayed_games_do_not_count():
def test_quality_values():                   # sin reseñas 0.75; 10/10 ≈ 0.7917; 10000/10000 > 0.99
def test_similar_pending_scores_higher():    # misma calidad; el que comparte tags con el más jugado gana
def test_explanation_anchor_and_shared_tags():
def test_pending_without_tags_has_no_anchor():
def test_only_pendiente_is_scored():
```

- [ ] **Step 2: Ejecuta y comprueba que fallan** — Run: `.venv/bin/pytest tests/test_taste.py -v` · Expected: FAIL.
- [ ] **Step 3: Implementa `src/taste.py`** según Interfaces.
- [ ] **Step 4: Ejecuta los tests** — Expected: PASS.
- [ ] **Step 5: Smoke test** — Run: `.venv/bin/python src/taste.py` (con los 5 juegos de la tarea 3) · Expected: crea `scores.json` sin errores.
- [ ] **Step 6: Commit** — `git add src/taste.py tests/ && git commit -m "taste.py: perfil de gusto por horas x tags y puntuación de pendientes"`

---

### Task 5: `recommender.py` — módulo puro de recomendación

**Files:**
- Create: `src/recommender.py`, `tests/test_recommender.py`

**Interfaces:**
- Consumes: el esquema de `games.json`, la salida de `score_pending`, las filas del backlog.
- Produces:
  - `@dataclass Filters(max_hours: float | None = None, include_tags: list[str] = [], exclude_tags: list[str] = [], min_positive: float | None = None, min_reviews: int | None = None, limit: int = 3)` (listas con `field(default_factory=list)`). `max_hours` filtra por `hltb_main <= max_hours` (duración total del juego); los juegos sin duración quedan fuera.
  - `@dataclass Recommendation(appid: str, name: str, score: float, reason: str, hours: float | None, positive_pct: int | None)` con la propiedad `url` → `https://store.steampowered.com/app/{appid}`.
  - `fold(s: str) -> str` (minúsculas, sin acentos); `matches(entry, term) -> bool` (`fold(term)` es subcadena de algún tag o género plegado).
  - `recommend(filters, games, scores, backlog) -> list[Recommendation]`: solo estado `pendiente`, ordenados por score. Para dar variedad, se salta un candidato cuya `anchor_appid` ya esté usada, salvo que no queden más (entonces rellena).
  - Texto de `reason`: `"porque jugaste {anchor_name} (comparten {a}, {b})"`; si no hay ancla, `"{pct} % de reseñas positivas"`; si tampoco hay reseñas, `"sin datos de reseñas"`.
  - `ROWS = (("Para ti", Filters(limit=5)), ("Sesión corta (≤ 5 h)", Filters(max_hours=5, limit=5)), ("Joyas valoradas (≥ 90 %)", Filters(min_positive=0.90, min_reviews=500, limit=5)))`; `netflix_rows(games, scores, backlog) -> list[tuple[str, list[Recommendation]]]`: ningún appid se repite entre filas (gana la fila anterior) y se omiten las filas vacías.
  - `top_tags(games, n: int = 80) -> list[str]`: tags más frecuentes en la biblioteca.

- [ ] **Step 1: Escribe los tests que fallan** (con `make_game` / `make_backlog`)

```python
def test_only_pendiente_recommended():
def test_max_hours_excludes_longer_and_unknown():   # A 3 h, B 20 h, C None; max_hours=5 → [A]   (Review Focus 1)
def test_include_tags_accent_and_case_insensitive():# "estrategia" ↔ género "Estrategia"; "rogue" ↔ tag "Roguelike"   (Review Focus 5)
def test_exclude_tags():
def test_min_positive_and_min_reviews():
def test_variety_skips_repeated_anchor():           # top1 y top2 con la misma ancla, top3 con otra: limit=2 → [top1, top3]; limit=3 → [top1, top3, top2]
def test_reason_texts():                            # las tres variantes exactas
def test_netflix_rows_no_duplicates_and_skips_empty():
def test_no_match_returns_empty_list():             # (Review Focus 3)
def test_top_tags_by_frequency():
```

- [ ] **Step 2: Ejecuta y comprueba que fallan** — Run: `.venv/bin/pytest tests/test_recommender.py -v` · Expected: FAIL.
- [ ] **Step 3: Implementa `src/recommender.py`** según Interfaces.
- [ ] **Step 4: Ejecuta los tests** — Expected: PASS.
- [ ] **Step 5: Commit** — `git add src/recommender.py tests/test_recommender.py && git commit -m "recommender.py: filtros, variedad y filas tipo Netflix"`

---

### Task 6: `llm.py` — capa conversacional con Claude

**Files:**
- Create: `src/llm.py`, `tests/test_llm.py`
- Modify: `.env.example` (añade `ANTHROPIC_API_KEY=`)

**Interfaces:**
- Consumes: `recommender.Filters`, `Recommendation`, `matches`/`fold`.
- Produces: `MODEL = "claude-haiku-5-5"`; `class LLMError(Exception)`; `parse_request(text: str, known_tags: list[str], client=None) -> Filters`; `write_intro(text: str, recs: list[Recommendation], client=None) -> str`. Con `client=None` se crea `anthropic.Anthropic()`. Cualquier excepción del SDK, la falta de clave o una respuesta vacía → `LLMError`.

- [ ] **Step 0: Carga el skill `claude-api`** antes de escribir código, para usar la forma actual del SDK (tool use forzado con `tool_choice`).

- [ ] **Step 1: Escribe los tests que fallan** — `FakeClient` con `.messages.create(**kw)` que guarda los kwargs y devuelve `SimpleNamespace(content=[...])`

```python
def test_parse_request_maps_tool_input():     # bloque tool_use set_filters {"max_hours": 10, "include_tags": ["Relaxing"]} → Filters(max_hours=10, include_tags=["Relaxing"])
def test_parse_request_drops_unknown_tags_and_clamps_limit():  # "Inventado" fuera; limit 9 → 5
def test_parse_request_forces_tool_and_model():  # kw["model"] == MODEL; kw["tool_choice"] fuerza set_filters
def test_client_error_raises_llmerror():
def test_write_intro_returns_text():
def test_write_intro_empty_raises():
```

- [ ] **Step 2: Ejecuta y comprueba que fallan** — Run: `.venv/bin/pytest tests/test_llm.py -v` · Expected: FAIL.
- [ ] **Step 3: Implementa `src/llm.py`**.
  - Herramienta `set_filters`: `max_hours` (number; "duración total máxima del juego en horas, según HowLongToBeat"), `include_tags`, `exclude_tags` (arrays de strings), `min_positive` (number, 0-1) y `limit` (integer, 1-5).
  - Prompt de sistema en español: traducir el ánimo a tags usando **solo** los de `known_tags` (se pasan en el prompt).
  - Al procesar la respuesta, se descartan los tags que no casan con ningún `known_tags` (vía `matches`) y `limit` se recorta a 1-5.
  - `write_intro`: máximo 2 frases en español, sin nombrar juegos fuera de la lista que se le pasa, `max_tokens=300`.
- [ ] **Step 4: Ejecuta los tests** — Expected: PASS.
- [ ] **Step 5: Commit** — `git add src/llm.py tests/test_llm.py .env.example && git commit -m "llm.py: texto libre a filtros e intro con Claude Haiku"`

---

### Task 7: `commands.py` + `git_sync.py` — lógica del bot sin Telegram

**Files:**
- Create: `src/commands.py`, `src/git_sync.py`, `tests/test_commands.py`, `tests/test_git_sync.py`

**Interfaces:**
- Consumes: `library` (`read_backlog`, `set_estado`, `ESTADOS`), `recommender` (todo), `llm` (`parse_request`, `write_intro`, `LLMError`), `config`.
- Produces:
  - `git_sync.commit_and_push(paths: list[str], message: str, repo: Path = ROOT) -> bool`. Usa un lock `fcntl.flock` sobre `data/.git.lock`. Si no hay cambios staged, devuelve `True` sin commitear. Si falla el push, devuelve `False` (el commit se queda en local) y lo registra en el log. CLI: `python src/git_sync.py "mensaje"` → commitea `backlog.csv`.
  - `@dataclass Reply(text: str, buttons: list[tuple[str, str]] = [])` con pares (etiqueta, callback_data); `@dataclass Store(games, scores, backlog)`; `load_store() -> Store` (relee los ficheros en cada llamada).
  - `render(title: str | None, recs) -> str`. Cada juego ocupa tres líneas: `"{i}. {name} — {hours or '?'} h · {pct or '?'} % 👍"`, luego `"   {reason}"` y luego `"   {url}"`. Texto plano, sin `parse_mode`.
  - Comandos: `cmd_ayuda() -> Reply`, `cmd_top(store)`, `cmd_recomienda(store, args: str)`, `cmd_buscar(store, args: str)` (≤ 10 resultados, con estado/duración/%), `cmd_marcar(store, args: str)`, `confirm_mark(appid, estado, backlog_path=BACKLOG_CSV, sync=commit_and_push) -> Reply`, `free_text(store, text, llm=llm_module) -> Reply`.
  - `parse_recomienda_args(args: str) -> Filters`: `"10h roguelike"` → `max_hours=10`, `include_tags=["roguelike"]`; acepta `1,5h` y `90min`.
  - `find_games(backlog, query, n=5) -> list[dict]`: si hay coincidencia exacta plegada, devuelve solo esa; si no, coincidencias por subcadena; si no, `difflib.get_close_matches` con `cutoff=0.6`.
  - Callback data: `"mark:{appid}:{estado}"` y `"cancel"`.

- [ ] **Step 1: Escribe los tests de `git_sync` que fallan** (repo temporal con remoto bare)

```python
def test_commit_and_push_creates_and_pushes_commit(tmp_path):
def test_no_changes_returns_true_without_commit(tmp_path):
def test_push_failure_returns_false_but_commits_locally(tmp_path):  # remoto borrado
```

- [ ] **Step 2: Escribe los tests de `commands` que fallan**

```python
def test_top_renders_rows():
def test_recomienda_parses_hours_and_tags():           # parse_recomienda_args("1,5h estrategia") → Filters(max_hours=1.5, include_tags=["estrategia"])
def test_recomienda_zero_results_message():            # "Nada encaja con esos filtros. Prueba con más horas o sin género."   (Review Focus 3)
def test_marcar_single_match_asks_confirmation():      # buttons == [("Sí", "mark:5:terminado"), ("Cancelar", "cancel")]; el backlog NO cambia
def test_marcar_ambiguous_offers_choices():            # >1 coincidencia → un botón por juego + Cancelar   (Review Focus 4)
def test_marcar_no_match_and_bad_estado():             # "No encuentro «x» en el backlog." / lista de estados válidos
def test_confirm_mark_writes_and_syncs():              # sync falso recibe (["backlog.csv"], "backlog: {name} → terminado")
def test_confirm_mark_reports_push_failure():          # sync → False; el texto avisa "(no se pudo subir a GitHub, queda en local)"
def test_free_text_uses_llm_filters():                 # llm falso; la respuesta es la intro + render(...)
def test_free_text_llm_error_falls_back_to_para_ti():  # "No he podido usar la IA ahora mismo; aquí van tus recomendaciones de siempre:"
def test_free_text_zero_results_falls_back():          # "No he encontrado nada exacto; esto es lo que más te encaja en general:"   (Review Focus 3)
```

- [ ] **Step 3: Ejecuta y comprueba que fallan** — Run: `.venv/bin/pytest tests/test_commands.py tests/test_git_sync.py -v` · Expected: FAIL.
- [ ] **Step 4: Implementa `src/git_sync.py` y `src/commands.py`** según Interfaces. `cmd_marcar` nunca escribe en el backlog; la escritura solo ocurre en `confirm_mark`.
- [ ] **Step 5: Ejecuta los tests** — Run: `.venv/bin/pytest -v` · Expected: PASS (toda la suite).
- [ ] **Step 6: Commit** — `git add src/commands.py src/git_sync.py tests/ && git commit -m "Comandos del bot y sincronización del backlog con git"`

---

### Task 8: `bot.py` — cableado de Telegram

**Files:**
- Create: `src/bot.py`, `tests/test_bot.py`
- Modify: `.env.example` (añade `TELEGRAM_BOT_TOKEN=` y `TELEGRAM_ALLOWED_CHAT_ID=`)

**Interfaces:**
- Consumes: `commands` (todo), `config.load_env`, `config.require_env`.
- Produces: `build_application(token: str, allowed_chat_id: int) -> telegram.ext.Application`; `main()`.
  - Handlers de comando, todos con `filters.Chat(chat_id=allowed)`: `start` y `ayuda` → `cmd_ayuda`; `top`; `recomienda`; `buscar`; `marcar`.
  - Handler de texto (`filters.TEXT & ~filters.COMMAND`) → `free_text`.
  - `CallbackQueryHandler` que comprueba que el chat del callback es el permitido. Con `cancel` edita el mensaje a "Cancelado."; con `mark:` llama a `confirm_mark` y edita el mensaje con el resultado.
  - En `group=1`, un handler de mensajes de cualquier otro chat que solo registra `"Chat no autorizado: {chat_id}"` en el log; sirve para averiguar tu chat_id la primera vez.
  - Cada handler: `await asyncio.to_thread(...)` sobre `load_store()` y el comando; los botones van en un `InlineKeyboardMarkup` con un botón por fila. Si algo lanza una excepción, se registra con `log.exception` y se responde "Algo ha fallado; mira el log del bot."
  - `main()`: `load_env()`, `require_env` de `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_CHAT_ID`, logging a stdout y `app.run_polling()`.

- [ ] **Step 1: Escribe el test que falla**

```python
def test_build_application_registers_commands():
    app = build_application("123:ABC", 42)
    cmds = {c for h in app.handlers[0] if isinstance(h, CommandHandler) for c in h.commands}
    assert cmds == {"start", "ayuda", "top", "recomienda", "buscar", "marcar"}
```

- [ ] **Step 2: Ejecuta y comprueba que falla** — Run: `.venv/bin/pytest tests/test_bot.py -v` · Expected: FAIL.
- [ ] **Step 3: Implementa `src/bot.py`** según Interfaces.
- [ ] **Step 4: Ejecuta los tests** — Expected: PASS.
- [ ] **Step 5: Smoke test manual (lo hace el usuario)**
  1. Crea el bot con @BotFather y pon `TELEGRAM_BOT_TOKEN` en `.env`.
  2. Arranca `.venv/bin/python src/bot.py` y escríbele desde el móvil. Copia al `.env` el chat_id que aparece en el log como "Chat no autorizado" y reinicia el bot.
  3. Prueba `/ayuda`, `/top`, `/marcar` (y cancela) y un texto libre.

  Expected: responde a todo; desde otra cuenta, el bot no responde.
- [ ] **Step 6: Commit** — `git add src/bot.py tests/test_bot.py .env.example && git commit -m "bot.py: bot de Telegram con long polling y allowlist"`

---

### Task 9: Operación en el Mac mini — job diario, launchd y README

**Files:**
- Modify: `src/export.py:105-109` (modo no interactivo), `README.md`
- Create: `scripts/daily.sh`, `ops/launchd/com.steambacklog.daily.plist`, `ops/launchd/com.steambacklog.bot.plist`, `ops/install_launchd.sh`, `tests/test_export_noninteractive.py`

**Interfaces:**
- Consumes: las CLI de `export.py`, `sync_backlog.py`, `enrich.py`, `taste.py` y `git_sync.py`.
- Produces:
  - `export.py` solo pide claves por consola si `sys.stdin.isatty()`. Sin TTY: si falta `STEAM_API_KEY` o `STEAM_ID` → `sys.exit("Falta STEAM_API_KEY en .env")` (o el nombre que falte); si falta el token familiar, se omite la biblioteca compartida con un aviso. Sigue siendo solo stdlib.
  - `scripts/daily.sh` (`set -euo pipefail`, se ejecuta desde la raíz del repo con `.venv/bin/python`):
    1. `export.py </dev/null || echo "⚠ export falló; sigo con los datos anteriores"`
    2. `sync_backlog.py` → `enrich.py` → `taste.py`
    3. `git_sync.py "backlog: sync diario"`
  - Plists con el marcador `__REPO__`:
    - el diario: `StartCalendarInterval` a las 06:00;
    - el bot: `RunAtLoad` + `KeepAlive` con `__REPO__/.venv/bin/python __REPO__/src/bot.py`;
    - ambos: `WorkingDirectory=__REPO__` y logs en `__REPO__/data/logs/{daily,bot}.log`.
  - `ops/install_launchd.sh`: sustituye `__REPO__` por la ruta absoluta del repo, crea `data/logs`, copia los plists a `~/Library/LaunchAgents`, ejecuta `launchctl bootout` (ignorando errores) y luego `launchctl bootstrap gui/$(id -u) <plist>`.

- [ ] **Step 1: Escribe el test que falla** — copia `src/export.py` a `tmp_path/src/` (así `ROOT` apunta a un directorio sin `.env`) y ejecútalo con `subprocess` usando `env={"PATH": ...}` y `stdin=DEVNULL`.

```python
def test_export_noninteractive_missing_key_exits(tmp_path):  # returncode != 0 y "STEAM_API_KEY" en stderr; no queda colgado (timeout=10)
```

- [ ] **Step 2: Ejecuta y comprueba que falla** — Run: `.venv/bin/pytest tests/test_export_noninteractive.py -v` · Expected: FAIL (`EOFError` o timeout).
- [ ] **Step 3: Implementa el modo no interactivo en `export.py`**, junto con `scripts/daily.sh`, los plists y `ops/install_launchd.sh`.
- [ ] **Step 4: Verifica**
  - Run: `.venv/bin/pytest -v && bash -n scripts/daily.sh ops/install_launchd.sh && .venv/bin/python -c "import plistlib,glob; [plistlib.load(open(p,'rb')) for p in glob.glob('ops/launchd/*.plist')]"` · Expected: todo PASS, sin errores de sintaxis.
  - Run: `scripts/daily.sh` (enriquece la biblioteca completa; la primera vez tarda ~30 min) · Expected: termina, `scores.json` contiene todos los pendientes, y o bien hay un commit "backlog: sync diario" pusheado o no había cambios.
- [ ] **Step 5: Actualiza el README** con:
  - instalación del venv;
  - todas las variables del `.env`;
  - creación del bot y cómo averiguar el chat_id;
  - la lista de comandos;
  - la instalación en el Mac mini: clonar, crear el venv, `.env`, credenciales de git para el push (`gh auth login`) y `ops/install_launchd.sh`;
  - la renovación del `STEAM_WEBAPI_TOKEN` (caduca en ~24 h; si caduca, se pierde la parte compartida del export, pero el perfil conserva las horas conocidas);
  - limitación conocida: si `/marcar` coincide con el job de las 06:00, puede perderse ese cambio de estado.
- [ ] **Step 6: Commit** — `git add -A scripts ops src/export.py README.md tests && git commit -m "Operación: job diario, servicios launchd y README del bot"`
