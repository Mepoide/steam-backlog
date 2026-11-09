# steam-backlog

Exporta la biblioteca de Steam (propia + compartida por Family Sharing),
la enriquece con géneros, tags, reseñas y duración, calcula un perfil de
gusto a partir de tus horas de juego y te recomienda qué jugar del backlog
desde un bot de Telegram.

Solo se versiona `backlog.csv` (appid, nombre, estado, prioridad, notas).
Nada de claves, SteamIDs ni horas de juego en git: los datos crudos y
derivados viven en `data/`, que está ignorado.

Cómo funciona por dentro (arquitectura, módulos y algoritmos de perfil y
recomendación): [docs/arquitectura.md](docs/arquitectura.md).

## Instalación

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # cp, no mv: .env.example está versionado
```

`src/export.py` y `src/sync_backlog.py` solo usan la librería estándar; el
resto necesita el venv. Ejecuta siempre desde la raíz del repo.

## Credenciales (`.env`)

- `STEAM_API_KEY`: en <https://steamcommunity.com/dev/apikey>.
- `STEAM_ID`: tu SteamID64 (17 dígitos, empieza por 7656). Lo ves en
  <https://steamid.io> o en la URL de tu perfil.
- `STEAM_WEBAPI_TOKEN` (opcional, para la biblioteca compartida): con la
  sesión de Steam iniciada en el navegador abre
  <https://store.steampowered.com/pointssummary/ajaxgetasyncconfig> y copia
  el valor de `webapi_token`. **Caduca en ~24 h**: si caduca, el export sigue
  solo con la biblioteca propia y el perfil conserva las últimas horas
  conocidas de los juegos compartidos. Renuévalo cuando quieras refrescarlas.
- `ANTHROPIC_API_KEY`: para el texto libre del bot (Claude Haiku 5.5, unos
  0,04 céntimos por consulta). Sin ella, el bot responde igual con sus
  recomendaciones de siempre.
- `LLM_MONTHLY_BUDGET_USD` (opcional, 1 $ por defecto; `0` = sin tope): tope de
  gasto mensual en IA. Ver "Control de costes".
- `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_CHAT_ID`: ver "Bot de Telegram".

## Pipeline

```bash
.venv/bin/python src/export.py        # → data/raw/steam_library.{csv,json}
.venv/bin/python src/sync_backlog.py  # juegos nuevos → backlog.csv como "pendiente"
.venv/bin/python src/enrich.py        # → data/enriched/games.json (incremental)
.venv/bin/python src/taste.py         # → data/enriched/profile.json y scores.json
```

O todo de una vez, más el commit/push de `backlog.csv`: `scripts/daily.sh`.

- `enrich.py` pide a la tienda de Steam (géneros), a `IStoreBrowseService`
  (tags con peso y reseñas, en español) y a HowLongToBeat (duración). Solo
  pide lo que falta; la primera vez tarda ~15 min para unos 400 juegos.
  `--limit N` para probar con pocos, `--refresh` para rehacerlo todo.
- `taste.py` construye tu perfil (horas × tags, con más peso a lo jugado en
  los últimos 6 meses) y puntúa cada juego pendiente con su motivo
  ("porque jugaste X"). El estado también cuenta: lo `terminado` pesa ×1.5,
  lo que estás `jugando` ×1.2, y lo `abandonado` o `descartado` resta (y nunca
  sale como "porque jugaste X"). El bot lo recalcula al marcar juegos.

Edita `backlog.csv` a mano o desde el bot: estado
(`pendiente|jugando|terminado|abandonado|descartado`), prioridad (1–5) y notas.
Solo se recomiendan los juegos en estado `pendiente`.

## Bot de Telegram

1. Crea el bot con [@BotFather](https://t.me/BotFather) y pon el token en
   `TELEGRAM_BOT_TOKEN`. Deja `TELEGRAM_ALLOWED_CHAT_ID` vacío.
2. Arranca `.venv/bin/python src/bot.py` y escríbele cualquier cosa desde tu
   móvil. El bot no te contestará, pero el log mostrará
   `Chat no autorizado: <número>`.
3. Copia ese número a `TELEGRAM_ALLOWED_CHAT_ID` y reinicia el bot. Desde
   entonces solo responde a ese chat.

Comandos:

- `/top`: tus filas "Para ti", "Sesión corta (≤ 5 h)" y "Joyas valoradas (≥ 90 %)".
- `/recomienda [duración] [géneros]`: p. ej. `/recomienda 10h roguelike, estrategia`.
  La duración es la del juego completo (HowLongToBeat); los géneros van
  separados por comas.
- `/buscar <texto>`: busca en el backlog y muestra estado, duración y reseñas.
- `/juego <nombre>`: ficha del juego: horas jugadas, última partida, fecha de
  compra (o de llegada a la biblioteca familiar), duración, reseñas y tags.
- `/parecidos <juego>`: lo que no has empezado que más se parece a ese juego
  (similitud de tags, pesando más los tags poco comunes).
- `/recientes [n]`: lo último que ha llegado a tu biblioteca, por fecha.
- `/marcar <juego> <estado>`: pide confirmación con botones, actualiza
  `backlog.csv` y hace commit + push.
- `/repasar [nombre]`: va enseñando, uno a uno, los juegos que ya empezaste (≥ 2 h) y
  siguen en «pendiente», lo último jugado primero, con botones Terminado /
  Jugando / Abandonado / Descartado / Saltar. Los cambios se suben a GitHub de
  una vez al pulsar Parar o al acabar (si lo dejas a medias, los sube el job diario).
  Con un nombre (`/repasar sherlock holmes`, o «repasemos los de Sherlock Holmes»)
  repasa solo los pendientes que casen, empezados o no.
- `/coste`: llamadas, tokens y dólares gastados en IA este mes, frente al tope.
- Texto libre: Claude decide si preguntas por un juego («cuánto he jugado al
  Hades» → ficha), por lo último que has comprado («qué he comprado hace poco»
  → recientes), por lo que te acaba de enseñar («¿esos los tengo?» → origen,
  duración y horas de esa lista), si cuentas cómo te va con juegos («me he
  pasado el Awakened, el Chapter One me aburre» → cambios de estado con un
  botón para confirmar) o qué jugar («algo tranquilo, estoy quemado», «rol por
  turnos parecido al Baldur's Gate 3» → filtros, el recomendador elige y Claude
  escribe una frase de introducción). También entiende «dame sus estados» o
  «¿cómo tengo los de Sherlock?», «de la biblioteca familiar», «el mejor valorado»,
  «lo más corto», «hola» o «cuánto llevo gastado». Claude nunca elige los juegos. Recuerda
  los 3 últimos turnos y los juegos que mostró en cada uno, así que entiende
  «¿y de estrategia?», «los dos últimos» o «márcalos».
- No se recomiendan juegos con 2 h o más jugadas aunque sigan en «pendiente»
  (el backlog nace con todo en «pendiente»; márcalos con `/marcar` cuando quieras). Con tags pedidos, el orden mezcla tu gusto con
  cuánto pesa cada tag en el juego, así que «tipo granja» no saca un juego que
  solo es un poco «Casual».

Las respuestas llevan imágenes de la tienda de Steam: álbum de cabeceras con las
recomendaciones, la cabecera con la ficha de `/juego` y el repaso con la imagen
de cada juego, que cambia en el mismo mensaje al pulsar los botones. Los estados
llevan emoji (⏳ 🎮 ✅ 💤 🚫). El job diario descarga además las imágenes a
`data/images/` para la futura web (`src/images.py`). Si el token de la biblioteca
familiar caduca, el job te avisa por Telegram con cómo renovarlo.

El bot usa long polling: no hace falta abrir puertos ni exponer nada. Al
arrancar publica su menú de comandos; la foto de perfil se pone una vez con
`.venv/bin/python ops/set_bot_photo.py` (icono en `assets/`, se regenera con
`assets/make_icon.sh`).

### Control de costes

Cada llamada a Claude guarda su `usage` real (tokens de entrada, salida y
caché) en `data/llm_usage.jsonl`, y el coste se calcula con una tabla de
precios que solo contiene los modelos que usa el bot. Si algún día se cambia de
modelo y no se añade su precio, esas llamadas se cuentan aparte como «sin
precio» en vez de estimarse. Al pasar el 80 % del tope mensual, el log avisa.
Al llegar al 100 %, el bot deja de llamar a la API hasta el mes siguiente y
sigue respondiendo con las recomendaciones sin IA.

## Mac mini (launchd)

```bash
git clone https://github.com/Mepoide/steam-backlog.git && cd steam-backlog
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # y rellénalo
git config --global user.name "Tu nombre"         # sin identidad git no hay commits
git config --global user.email "tu@correo.example"
gh auth login           # credenciales para que el job pueda hacer git push
scripts/daily.sh        # primera ejecución a mano (~15 min)
ops/install_launchd.sh  # job diario a las 06:00 + bot siempre activo
```

Logs en `data/logs/{daily,bot}.log`. El del bot registra cada mensaje, los
filtros que sacó Claude y los juegos recomendados, para poder revisar la calidad. Reinicia el bot tras cambiar `.env`:
`launchctl kickstart -k gui/$(id -u)/com.steambacklog.bot`.

Antes de subir, `git_sync` integra los cambios remotos (`pull --rebase`), así
que puedes editar `backlog.csv` desde otro equipo. Si hay un conflicto en la
misma línea, el commit se queda en local y el log lo avisa.

**Limitación conocida:** si haces un `/marcar` justo mientras corre el job
de las 06:00, ese cambio de estado puede perderse.

## Aviso

Varias fuentes no son APIs oficiales y pueden cambiar o dejar de funcionar
sin previo aviso: la biblioteca compartida (`IFamilyGroupsService`), los tags
y reseñas (`IStoreBrowseService`, semidocumentada) y HowLongToBeat.
