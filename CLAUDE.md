# AI World Studio — guía para agentes (léelo primero)

**El protagonista es el generador, no Godot.** Godot es un destino de exportación: los mundos deben salir como paquetes autocontenidos que se sueltan
en cualquier proyecto de Godot 4 (`World.tscn` solo con el mundo; `Preview.tscn` opcional). No acoples el generador a un proyecto concreto.
El plan maestro es `AI_World_Studio_Plan_Maestro_Python.md` (§28 = estado y próximos pasos). Habla con el usuario en español.

Objetivo del usuario: generar escenarios tipo Genshin Impact / Zelda BOTW (mesetas, precipicios, ríos, cascadas, llanuras, bosques, senderos, montañas
escalables) y llevarlos a cualquier proyecto de Godot. Su juego (Bowgame / *Wicktale*; en macOS era la carpeta `prubas-3d`, **en Windows es `~/Desktop/Proyectos/Godot/wicktale`**) usa Terrain3D 1.1.0, jugador en
capa 2, Forward+, Jolt; ahí instalamos mundos de prueba en `generated_worlds/` (archivos nuevos, nunca modificar sus escenas ni `project.godot`).
Su terreno: `tallow_hills.tscn`, `Terrain3DMaterial` incrustado con `shader_override` = `res://shaders/terrain3d_toon.gdshader` (sin texturas; pinta pasto,
roca por pendiente y camino donde la textura base es la id 1). Receta para instalar un mundo con su aspecto:
`export_to_godot(godot_project=<wicktale>, subfolder="generated_worlds/<nombre>", terrain="terrain3d", terrain3d_material="res://tallow_hills.tscn",
terrain3d_texture_ids={"dirt": 1, resto: 0}, water_script="res://scripts/world/water.gd")` (agua del juego: nada, vadea, sus cascadas toon). Instalados: `generated_worlds/highland_lake_t3d` (1024 m, semilla 3) y `highland_lake_s7` (1536 m, semilla 7) con la escena de prueba
`generated_worlds/test_highland_lake_s7.tscn` y `test_highland_lake_s7_lago.tscn` (en la orilla del lago) (World + su sol + `world_env_open.tres` + Player armado como en `world_test.tscn`, en PlayerStart;
`start_yaw` = atan2(dx, dz): el modelo mira a +Z). Probado jugando por MCP `godot-ai` (acciones `move_forward`, `run`, `jump`).
Se trabaja en Windows: Godot 4.7.2 en `~/Downloads/Godot_v4.7.2-stable_win64.exe/…_console.exe` (lo encuentra `godot_bin.find_godot`).

## Cómo se usa
- Agente: herramientas MCP `ai-world-studio` (`.mcp.json`; si no aparecen, reiniciar la sesión). **Si el editor está abierto** (`awstudio.py ui`),
  el servidor MCP reenvía cada herramienta al editor (`core/world/bridge.py`, `~/.ai_world_studio/editor.json` con puerto y token, solo 127.0.0.1):
  agente y usuario trabajan sobre el MISMO mundo y el panel «Agente» muestra cada llamada; `AWS_EDITOR=off` lo desactiva o CLI: `python awstudio.py call <tool> '<json>'`.
  `python awstudio.py tools` lista las 56 herramientas; `docs/TOOLS.md` las documenta (`python awstudio.py docs` lo regenera).
- Rápido: `python awstudio.py generate highland_lake --size 1024 --seed 3 --save X.awp --godot-project <proyecto> --subfolder generated_worlds/nombre`.
- UI: `python awstudio.py ui` (Archivo → Generar paisaje de ejemplo Ctrl+G; Exportar a Godot Ctrl+E). REST: `awstudio.py api`.
- Tests: `pytest -q` (≈3 min; incluye importaciones, navegación y Terrain3D en Godot real si `find_godot` lo encuentra / `$AWS_GODOT`, y el addon
  Terrain3D en `$AWS_TERRAIN3D_ADDON` o en un proyecto de `~/Desktop/Proyectos/Godot/*`). Estado: 107 en verde (≈2,8 min; incluye el editor PySide6 sin pantalla y el puente editor↔agente). Venv: `.venv/Scripts/python.exe`.

## Mapa del código
`core/world/session.py` (WorldSession: operaciones, undo, checkpoints) y `service.py` (las 56 herramientas, única fuente para UI/CLI/REST/MCP; el editor comparte la misma `SESSION`) ·
`core/world/presets.py` (`highland_lake`) · `core/history/operations.py` (registro de operaciones, replay) · `terrain/` (heightfield, pinceles, procedural,
erosión, chunks) · `features/` (curvas, `rivers/river.py` con cascadas, lagos, `paths/` con `routing.py`: rutas, `route_river`, transitabilidad) ·
`geometry/cliffs` (muralla de mesetas) · `biomes/` (máscaras, dispersión) · `materials/` · `validation/` · `render/preview.py` (vistas headless para agentes) ·
`exporters/godot/` (`exporter.py`, `glb.py`, `terrain_lod.py`, `navigation.py`, `procedural_assets.py`, `assets_bake.py`, `library/*.res`, `shaders/`) ·
`api/` · `mcp_server/` · `ui/` + `app/` · `tests/`.

## Flujo recomendado para construir un mundo
1. `get_capabilities`. Unidades: metros; origen noroeste; x este, y sur; celdas ≈2 m; (vértices−1) divisible por 4.
2. `generate_landscape("highland_lake", size_m, seed)` o a medida: `create_world` → `apply_terrain_noise` → `create_bowl`/`create_mountain*` → `terrace_terrain` →
   `create_border_mountains` → `create_lake` → `create_river_auto` (cascadas) → `create_climbable_mountain` → `create_trail` → `scatter_vegetation(presets)`.
3. `create_checkpoint` antes de cambios grandes. 4. **Mira el resultado**: `capture_world_view` + `view_image`. 5. `validate_world` + `analyze_traversability`.
6. `save_world` y `export_to_godot(godot_project=…, subfolder=…)` (con `terrain="terrain3d"` si el proyecto usa Terrain3D); luego en Godot:
   re-escanear, instanciar `World.tscn` o abrir `Preview.tscn`.

## Reglas del dominio
- Las herramientas devuelven `{"ok": false, "error": …}`; corrige parámetros, no reintentes igual.
- Ríos/lagos/senderos dependen del terreno: si lo cambias después, rehazlos (aún no se marcan como desactualizados).
- No erosión térmica tras `terrace_terrain`. Rampas/senderos transitables ≥ 6 m de ancho. Montañas escalables: solo `create_climbable_mountain`
  (los senderos auto-enrutados por laderas muy empinadas hacen zigzags que se solapan; avisan `path_too_steep`).
- `export_to_godot` se niega a escribir en carpetas ajenas no vacías: no lo fuerces. Con `godot_project` no se toca `project.godot`.
- La navegación se hornea con Godot (Recast) en una **copia temporal**; la malla de Python (`navigation="python"`) es gruesa y desconecta senderos estrechos.

## Lecciones técnicas (no repetir estos errores)
- Terrain3D: las regiones las escribe Godot con el addon del proyecto (copia temporal), nunca Python. `change_region_size` con el nodo ya en el árbol.
  En juego vacía `texture_list` tras crear los arrays (verificar con `get_albedo_array_rid`). Comparar alturas solo en vértices (interpola por triángulo).
  `terrain3d_material` acepta una escena `.tscn`: se copia su `Terrain3DMaterial` (rutas externas intactas, `world_background = 0`); las copias temporales
  (`_temp_project`) traen esas dependencias del proyecto receptor (`copy_external_deps`). Las capturas de paquetes incrustados también van en copia temporal.
  Para ver el aspecto real del juego, capturar con su entorno (`world_env_open.tres`) y su sol, no con `Preview.tscn` (lava los colores toon).
- Agua como datos (`exporters/godot/game_water.py`, siempre en `WorldData/water.json` + `surface.r32`, formato del `water.gd` del juego): cinta de
  muestras `[x,y,z,izq,der,caída]` cada 3 m, cascadas simuladas (port de `gen_tallow_hills.simulate_falls`), lagos en celdas de 4 m, rejilla de superficie.
  Nuestros cauces son poco profundos y van por terrazas: la orilla se mide con tope (ancho/2 + 4 m) y se corta en los precipicios (si no, láminas colgando);
  las cascadas toman el ancho de aguas arriba (en el borde la medición sale corta); el agua que cae en un escalón plano casi a nivel de la poza termina ahí;
  las celdas de río con suelo > 4,5 m bajo el agua no son agua (borde de terraza junto al río: nadaría en el aire).
- Edición de operaciones pasadas: `session.modify(i, params)` / `modify_operation` repite TODO el historial desde `session.origin` (terreno antes de
  cualquier operación; se guarda en el `.awp` como `origin.npy`; proyectos viejos sin él no se pueden editar). Por eso todo cambio de terreno debe
  quedar como operación: las pinceladas del editor se registran como `brush` (lista de dabs). Repetir sin cambios da el terreno idéntico.
  Tras cambiar el terreno, `session.reground()` reasienta vegetación y objetos (antes flotaban hasta reabrir).
- `water_mask` rasteriza los lagos con PIL dentro de su caja (antes `_poly_sdf` sobre todo el mapa: 10 s en 1536 m; ahora 0,16 s). Si algo es lento,
  perfilar antes de cachear: suele ser un cálculo por celda sobre todo el mapa.
- `highland_lake`: el río apunta a la orilla cercana del lago (en el centro está la isla y un río no sube a ella) y río y sendero de terrazas
  reintentan por el borde; 20/20 semillas probadas salen bien. Los clientes MCP lanzan el servidor con un entorno mínimo: variables propias
  (p. ej. `AWS_EDITOR_LOCK`) hay que pasarlas en `env` explícitamente.
- **Godot headless NO compila shaders ni dibuja**: para ver el resultado usa `screenshot_with_godot` (ventana real) o el editor vía MCP. Verifica siempre con capturas.
- No guardar pesos en el canal alfa de PNG (Godot "arregla" el RGB de píxeles transparentes → rampas negras): splat maps en RGB opaco (3 texturas).
- Texturas usadas en 3D pueden recomprimirse con pérdida: datos exactos (alturas) van en `.tres` ImageTexture (RG8 en base64), no en PNG.
- Colores de vértice en sRGB hay que convertirlos a lineal en el shader; la niebla volumétrica de 4.7 es muy fuerte (se quitó).
- `NavigationServer3D.map_get_path` corta a 4096 polígonos: usar `query_path` con `path_search_max_polygons` alto. El mapa tarda ~100 fotogramas en sincronizar.
- La colisión debe tener la misma resolución que la malla visual/navegación (`collision_step=1`), o el personaje se atasca en rampas.
- Ejecutar Godot sobre el proyecto receptor lo reimporta entero: usar `_temp_project` (copia temporal) para hornear/verificar.
- `MultiMesh` necesita `custom_aabb` si el shader desplaza vértices; la hierba va por GPU (nada por mata en la escena).
- En macOS `sed -i` necesita `''`; con bloques largos usar scripts de Python para editar. Un `&&` tras un comando que falla salta TODA la edición siguiente (pasó una vez).
- MCP `godot-ai`: `game_eval` aborta a los 8 s (y deja el juego en depurador: parar y relanzar); el juego se congela en segundo plano (acelerar con
  `Engine.time_scale`); `editor_screenshot` falla si la imagen supera ~1 MB (bajar `max_resolution`).

## Pendiente (orden sugerido; detalle en el plan §28)
1. ~~Exportar a Terrain3D~~ (hecho y probado en `wicktale` con su material toon; falta que el usuario lo recorra con su jugador).
2. ~~Agua del juego~~ (hecho: `water_script`). Siguiente: usar `flora.gd` y `grass_field.gd` del juego (vegetación con colisión, pasto y flores);
   senderos con su propia id de textura (hoy `dirt` = id 1). Flores y más hierba.
3. Formaciones de roca (arcos, pilares, salientes); acantilados como geometría real en las terrazas. 4. Más presets (cañón, costa, pradera abierta).
5. Editar features creados (`modify_*`), dependencias y aviso de "desactualizado"; reordenar/desactivar operaciones. 6. UI: ~~diálogo de exportación~~, ~~colocar objetos~~, ~~editar curvas (ríos, senderos, lagos)~~ (hechos). Faltan: editar otras operaciones
   desde la UI ~~(hecho: panel Historial con parámetros y activar/desactivar)~~, ~~overlays~~ (altura, pendiente, agua, transitable). Faltan:
   ~~panel de actividad del agente~~ (hecho: puente editor↔MCP). Faltan: pinceles nuevos, biblioteca de assets, modo «asistido» (el agente
   propone y el usuario aprueba).
7. Cascadas con colisión/sonido/salpicaduras; ríos que se unen. 8. Validador completo, AWP completo, procedencia de assets, probar otras versiones de Godot (solo 4.7.2 probado).

## Desarrollo
Shaders: `exporters/godot/shaders/`. Props procedurales: `procedural_assets.py` → `python -m exporters.godot.assets_bake` (necesita Godot) → `library/*.res` (commit).
Documentación viva: `README.md`, `docs/sprint1.md` (decisiones y límites por fase), `docs/TOOLS.md`. Repo: github.com/gerardolucero1/AI_World_Studio (rama `main`).
