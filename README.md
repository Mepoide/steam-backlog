# steam-backlog

Exporta la biblioteca de Steam (propia + compartida por Family Sharing),
la enriquece y mantiene un backlog personal para decidir qué jugar.

Solo se versiona `backlog.csv` (appid, nombre, estado, prioridad, notas).
Nada de claves, SteamIDs ni horas de juego en git: los datos crudos viven
en `data/`, que está ignorado.

## Credenciales

Copia `.env.example` a `.env` y rellena:

- `STEAM_API_KEY`: en <https://steamcommunity.com/dev/apikey>.
- `STEAM_ID`: tu SteamID64 (17 dígitos, empieza por 7656). Lo ves en
  <https://steamid.io> o en la URL de tu perfil.
- `STEAM_WEBAPI_TOKEN` (opcional, para la biblioteca compartida): con la
  sesión de Steam iniciada en el navegador abre
  <https://store.steampowered.com/pointssummary/ajaxgetasyncconfig> y copia
  el valor de `webapi_token`. **Caduca en ~24 h**; renuévalo cuando falle.

`python-dotenv` es opcional: sin él, exporta las variables en el entorno.

## Uso

```bash
python src/export.py        # → data/raw/steam_library.{csv,json}
python src/sync_backlog.py  # añade juegos nuevos a backlog.csv como "pendiente"
```

Después edita `backlog.csv` a mano: estado
(`pendiente|jugando|terminado|abandonado|descartado`), prioridad (1–5) y notas.

`src/enrich.py` (género, duración, valoración) es un stub pendiente.

## Aviso

La API de la biblioteca compartida (`IFamilyGroupsService`) no está
documentada por Valve y puede cambiar o dejar de funcionar sin previo aviso.
